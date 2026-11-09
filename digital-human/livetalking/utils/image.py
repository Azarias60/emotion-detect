
import cv2
import time
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import OrderedDict
from threading import RLock


class LazyFrameList:
    """Memory-efficient frame list: loads images from disk on demand with LRU cache.

    Avoids loading all 12+ GB of 1080p frames into RAM at once. Only the
    most recently accessed frames are kept in memory.

    预取是**并行**的：PNG 解码是 CPU 密集，OpenCV 解码时会释放 GIL，
    实测 4 线程 3.9x、8 线程 6.6x（11.0 ms/帧 → 1.7 ms/帧）。
    """

    # 预取线程数。8 线程实测 6.6x 加速，再多收益递减（受 IO/内存带宽限制）。
    PREFETCH_WORKERS = 8

    def __init__(self, img_paths, cache_size=50):
        self._paths = list(img_paths)
        self._cache = OrderedDict()
        self._cache_size = cache_size
        self._lock = RLock()
        self.shape = None  # populated on first access
        # 诊断计数：缓存命中率 + 落在**调用线程**上的同步读盘耗时。
        # 切换段时如果 miss_seconds 一直涨，说明预取没跟上 —— 那会直接
        # 表现为画面卡顿（渲染线程每帧只有 40ms 预算，一帧读盘要 11ms）。
        self.hits = 0
        self.misses = 0
        self.miss_seconds = 0.0

    def __len__(self):
        return len(self._paths)

    def _cached(self, idx):
        with self._lock:
            frame = self._cache.get(idx)
            if frame is not None:
                self._cache.move_to_end(idx)
            return frame

    def _put(self, idx, frame):
        with self._lock:
            existing = self._cache.get(idx)
            if existing is not None:
                self._cache.move_to_end(idx)
                return existing
            self._cache[idx] = frame
            if self.shape is None:
                self.shape = frame.shape
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
            return frame

    def __getitem__(self, idx):
        if isinstance(idx, slice):
            return [self[i] for i in range(*idx.indices(len(self)))]
        if idx < 0:
            idx += len(self._paths)
        frame = self._cached(idx)
        if frame is not None:
            self.hits += 1
            return frame
        # 未命中：这里是在**调用线程**（渲染线程）上同步读盘。
        # 一帧 720x1280 PNG 约 11ms，25fps 每帧预算只有 40ms。
        started = time.perf_counter()
        frame = cv2.imread(self._paths[idx])
        self.misses += 1
        self.miss_seconds += time.perf_counter() - started
        if frame is None:
            raise FileNotFoundError(f"Failed to load {self._paths[idx]}")
        return self._put(idx, frame)

    def preload_range(self, start, end):
        """Preload a contiguous range of frames (useful for actions).

        并行解。原来是顺序一帧一帧解，实测 11.0 ms/帧：状态机在过渡播放
        期间要同时预取"过渡段 + 目标段"，最多 400+ 帧 = 4.6 秒，而过渡
        只播 3.6 秒 —— 预取根本来不及，来不及的帧就落回渲染线程同步读盘，
        表现就是切换时卡顿。并行后同一批只要 ~0.7 秒。
        """
        start = max(0, start)
        end = min(end, len(self._paths))
        if end <= start:
            return
        with self._lock:
            todo = [i for i in range(start, end) if i not in self._cache]
        if not todo:
            return

        def load(i):
            frame = cv2.imread(self._paths[i])
            return None if frame is None else (i, frame)

        workers = min(self.PREFETCH_WORKERS, len(todo))
        if workers <= 1:
            for i in todo:
                item = load(i)
                if item is not None:
                    self._put(*item)
            return
        # 用 as_completed 而不是 executor.map：map 会按顺序缓冲结果，
        # 前面几帧慢的话后面几百帧的解码结果全堆在内存里（每帧 2.64 MB，
        # 400 帧就是 1 GB）。as_completed 边完成边入缓存，峰值受线程数限制。
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(load, i) for i in todo]
            for fut in as_completed(futures):
                item = fut.result()
                if item is not None:
                    self._put(*item)

    def cached_count(self):
        with self._lock:
            return len(self._cache)

    def stats(self):
        total = self.hits + self.misses
        return {
            "cached": self.cached_count(),
            "cache_size": self._cache_size,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": (self.hits / total) if total else 1.0,
            "miss_seconds": round(self.miss_seconds, 3),
        }


def read_imgs(img_list):
    """Load all images into memory. Use LazyFrameList for large datasets."""
    def load_image(index, img_path):
        return index, cv2.imread(img_path)

    frames = [None] * len(img_list)  # Initialize a list with the same length as img_list
    with ThreadPoolExecutor() as executor:
        futures = {executor.submit(load_image, idx, img_path): idx for idx, img_path in enumerate(img_list)}
        for future in tqdm(as_completed(futures), total=len(img_list)):
            idx, img = future.result()
            frames[idx] = img
    return frames


def lazy_read_imgs(img_list, cache_size=50):
    """Create a memory-efficient lazy-loading frame list."""
    return LazyFrameList(img_list, cache_size=cache_size)

def mirror_index(size, index):
    turn = index // size
    res = index % size
    if turn % 2 == 0:
        return res
    else:
        return size - res - 1

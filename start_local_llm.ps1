# 启动本地大模型服务 (Ternary-Bonsai-2-27B)
# 服务端口: 1919 (OpenAI 兼容接口: http://127.0.0.1:1919/v1)

$env:GGML_CUDA_GRAPH_OPT = "1"
& "C:\docu\app\PrismML-Eng-llama\llama-server.exe" `
  -m "C:\Users\99507\.cache\huggingface\hub\models--prism-ml--Ternary-Bonsai-2-27B-gguf\snapshots\6ed5e12bf84b7a63069882c91dd9e9218647d17b\Ternary-Bonsai-2-27B-PQ2_0.gguf" `
  --flash-attn on --spec-type ngram-mod `
  -c 32768 -np 1 `
  --host 0.0.0.0 --port 1919

# constitution_model.py — 中医体质分类与判定 数据模型
# 来源：GB/T 46939—2025《中医体质分类与判定》国家标准
# 发布：2025-12-31  实施：2026-04-01
# 主要起草人：王琦 等
# ==============================================================================
# 功能:
#   接收 4 维舌象检测结果，通过 36 条硬编码规则表匹配 9 种中医体质类型，
#   输出候选体质列表和完整的身心评估报告。
#
# 核心对外接口 (3 个):
#   1. infer_constitution_from_tongue(color, coated, crack, indent) → list
#      输入: 4 个英文标签字符串
#         color:   "red"|"white"|"crimson"       ← 来自 TonguePredictor color.class
#         coated:  "white"|"yellow"|"nocoated"   ← 来自 TonguePredictor coated.class
#         crack:   "crack"|"normal"              ← 来自 TonguePredictor crack.class
#         indent:  "indentation"|"normal"        ← 来自 TonguePredictor indentation.class
#      输出: [{id, name, english, weight, category}, ...] 按 weight 降序排列
#
#   2. get_body_mind_report(constitution_id) → dict
#      输入: 体质 ID (1-9)
#      输出: {constitution_name, body_signals, mind_summary,
#             disease_risk, advice: {diet, exercise, lifestyle, mind}, ...}
#
#   3. get_constitution_advice(constitution_id) → dict
#      输入: 体质 ID (1-9)
#      输出: {diet, exercise, lifestyle, mind} 四方面调理建议
#
# 内部数据结构:
#   CONSTITUTION_TYPES          — 9 种体质 ID→名称
#   CONSTITUTION                — 9 种体质完整定义 (身体/心理/舌象/疾病风险)
#   TONGUE_TO_CONSTITUTION      — 36 条舌象→体质映射规则，每条含权重 (1-3)
#   SCALE_QUESTIONS             — 26 题体质量表 (本模块含数据，但项目未调用)
#
# 辅助接口 (本项目中未使用):
#   judge_constitution(scores)  — 量表判定算法
#   calc_transformed_score()    — 原始分→转化分
#
# 依赖: 无外部依赖，纯 Python 标准库 (dict/list/str/int)
# 被依赖: services/video_service.py, efficient_net/predictor.py
#
# 判定逻辑:
#   - 非 ML 分类，是规则匹配
#   - 3×3×2×2 = 36 种舌象组合全覆盖，无拒识
#   - weight: 3=核心命中, 2=强关联, 1=可能关联
# ==============================================================================

# ==================== 9 种体质类型 ====================

# 注：平和质为正常体质，其余 8 种为"偏颇体质"

# 9 种体质 ID → 中文名称
# 体质 ID 是贯穿整个模块的统一标识符，所有映射表、算法均以此为 key
CONSTITUTION_TYPES = {
    1:  "平和质",
    2:  "气虚质",
    3:  "阳虚质",
    4:  "阴虚质",
    5:  "痰湿质",
    6:  "湿热质",
    7:  "血瘀质",
    8:  "气郁质",
    9:  "特禀质",
}

CONSTITUTION_EN = {
    1:  "Balanced",
    2:  "Qi Deficiency",
    3:  "Yang Deficiency",
    4:  "Yin Deficiency",
    5:  "Phlegm-Dampness",
    6:  "Damp-Heat",
    7:  "Blood Stasis",
    8:  "Qi Stagnation",
    9:  "Allergic (Special)",
}

# ==================== 9 种体质完整定义 ====================
# 每种体质的结构化数据，包含以下字段：
#   name / english          — 中英文名称
#   category                — "normal"(平和质) 或 "biased"(偏颇体质)
#   key_features            — 一句话核心特征描述
#   body_shape              — 典型体型
#   common_signs            — 常见临床表现（含关键舌象、脉象）
#   psychological           — 心理特征量化：每个维度 0-3 分，0=无，3=严重
#       irritability        易怒/烦躁
#       anxiety             焦虑/紧张
#       overthinking        思虑过度
#       low_mood            情绪低落/精神不振
#       sensitivity         敏感多疑
#       cognitive_note      (仅血瘀质) 标志性心理特征备注
#   disease_tendency        — 易患疾病列表
#   environment             — 环境适应能力描述

CONSTITUTION = {
    1: {  # 平和质 — 健康基准
        "name": "平和质",
        "english": "Balanced",
        "category": "normal",  # 正常体质
        "key_features": "阴阳气血调和，体态适中、面色红润、精力充沛",
        "body_shape": "体形匀称健壮",
        "common_signs": {
            "face":           "面色、肤色润泽",
            "hair":           "头发稠密有光泽",
            "eyes":           "目光有神",
            "nose":           "鼻色明润，嗅觉通利",
            "lips":           "唇色红润",
            "energy":         "精力充沛，不易疲劳",
            "temperature":    "耐受寒热",
            "appetite":       "胃纳佳",
            "sleep":          "睡眠良好",
            "urine_stool":    "二便正常",
            "tongue":         "舌淡红，苔薄白",          # ← 舌象标准
            "pulse":          "脉和缓有力",
        },
        "psychological": {
            "summary":     "性格随和开朗",
            "irritability":    0,  # 易怒 0-3
            "anxiety":          0,  # 焦虑
            "overthinking":     0,  # 思虑过度
            "low_mood":         0,  # 情绪低落
            "sensitivity":      0,  # 敏感多疑
        },
        "disease_tendency": [
            "平素患病较少",
        ],
        "environment": "对自然环境和社会环境适应能力较强",
    },

    2: {  # 气虚质
        "name": "气虚质",
        "english": "Qi Deficiency",
        "category": "biased",
        "key_features": "元气不足，以疲乏、气短、自汗为主要特征",
        "body_shape": "肌肉松软不实",
        "common_signs": {
            "voice":          "平素语音低弱，气短懒言",
            "energy":         "容易疲乏，精神不振",
            "sweat":          "易出汗",
            "tongue":         "舌淡红，舌边有齿痕",      # ← 关键舌象
            "pulse":          "脉弱",
        },
        "psychological": {
            "summary":     "性格内向，不喜冒险",
            "irritability":    0,
            "anxiety":          0,
            "overthinking":     0,
            "low_mood":         1,  # 精神不振（偏气虚），但非抑郁低落
            "sensitivity":      0,
        },
        "disease_tendency": [
            "易患感冒、内脏下垂",
            "肥胖、骨质疏松",
            "老年便秘、慢性疲劳综合征",
            "焦虑症",
        ],
        "environment": "不耐受风、寒、暑、湿、热邪",
    },

    3: {  # 阳虚质
        "name": "阳虚质",
        "english": "Yang Deficiency",
        "category": "biased",
        "key_features": "阳气不足，以畏寒怕冷、手足不温等虚寒表现为主要特征",
        "body_shape": "肌肉松软不实",
        "common_signs": {
            "cold_aversion":  "平素畏冷，手足不温",
            "diet":           "喜热饮食",
            "energy":         "精神不振",
            "tongue":         "舌淡胖嫩，或舌边有齿痕",  # ← 关键舌象
            "pulse":          "脉沉迟",
        },
        "psychological": {
            "summary":     "性格多沉静、内向",
            "irritability":    0,
            "anxiety":          0,
            "overthinking":     0,
            "low_mood":         1,  # 沉静寡言≠情绪低落
            "sensitivity":      0,
        },
        "disease_tendency": [
            "易患痰饮、肿胀、泄泻",
            "骨质疏松、慢性疲劳综合征",
            "原发性痛经、膝关节炎",
        ],
        "environment": "耐夏不耐冬；易感风、寒、湿邪",
    },

    4: {  # 阴虚质
        "name": "阴虚质",
        "english": "Yin Deficiency",
        "category": "biased",
        "key_features": "阴液亏少，以口燥咽干、手足心热等虚热表现为主要特征",
        "body_shape": "体形偏瘦",
        "common_signs": {
            "heat":           "手足心热",
            "eyes":           "眼睛干涩",
            "mouth":          "口燥咽干，鼻微干",
            "drink":          "喜冷饮",
            "stool":          "大便干燥",
            "tongue":         "舌红少津",                 # ← 关键舌象
            "pulse":          "脉细数",
        },
        "psychological": {
            "summary":     "性格活泼，性情急躁，外向好动",
            "irritability":    3,  # 急躁
            "anxiety":          1,
            "overthinking":     0,
            "low_mood":         0,
            "sensitivity":      0,
        },
        "disease_tendency": [
            "虚劳、遗精、不寐（失眠）",
            "骨质疏松症、糖尿病",
            "高血压、偏头痛",
            "功能性便秘、肌少症、干眼症",
        ],
        "environment": "耐冬不耐夏；不耐受暑、热、燥邪",
    },

    5: {  # 痰湿质
        "name": "痰湿质",
        "english": "Phlegm-Dampness",
        "category": "biased",
        "key_features": "痰湿凝聚，以形体肥胖、腹部肥满、口黏苔腻为主要特征",
        "body_shape": "体形肥胖，腹部肥满松软",
        "common_signs": {
            "face":           "面部皮肤油脂较多",
            "sweat":          "多汗且黏",
            "chest":          "胸闷",
            "phlegm":         "痰多",
            "mouth":          "口黏腻或甜",
            "diet":           "喜食肥甘甜黏",
            "tongue":         "舌胖大，苔腻",             # ← 关键舌象
            "pulse":          "脉滑",
        },
        "psychological": {
            "summary":     "性格偏温和、稳重，善于忍耐",
            "irritability":    0,
            "anxiety":          0,
            "overthinking":     0,
            "low_mood":         0,
            "sensitivity":      0,
        },
        "disease_tendency": [
            "消渴（糖尿病）、中风、胸痹",
            "失眠、代谢综合征",
            "肥胖、高血压、高脂血症",
            "脂肪肝、肝硬化、高尿酸血症",
            "肠息肉、多囊卵巢综合征",
        ],
        "environment": "对梅雨季节及潮湿环境适应能力差",
    },

    6: {  # 湿热质
        "name": "湿热质",
        "english": "Damp-Heat",
        "category": "biased",
        "key_features": "湿热内蕴，以面垢油光、口苦、苔黄腻为主要特征",
        "body_shape": "形体中等或偏瘦",
        "common_signs": {
            "face":           "面垢油光，易生痤疮",
            "mouth":          "口苦口干",
            "body":           "身重困倦",
            "stool":          "大便黏滞不畅或燥结",
            "urine":          "小便短黄",
            "male":           "阴囊潮湿",
            "female":         "带下色黄量多",
            "tongue":         "舌质偏红，苔黄腻",          # ← 关键舌象
            "pulse":          "脉滑数",
        },
        "psychological": {
            "summary":     "容易心烦急躁",
            "irritability":    3,  # 心烦急躁
            "anxiety":          1,
            "overthinking":     0,
            "low_mood":         0,
            "sensitivity":      0,
        },
        "disease_tendency": [
            "痤疮、黄疸、热淋",
            "不寐（失眠）、消渴（糖尿病）",
            "高尿酸血症、非酒精性脂肪性肝病",
            "肝硬化、肠息肉",
        ],
        "environment": "对夏末秋初湿热气候，潮湿或气温偏高环境较难适应",
    },

    7: {  # 血瘀质
        "name": "血瘀质",
        "english": "Blood Stasis",
        "category": "biased",
        "key_features": "血行不畅，以肤色晦暗、舌质紫暗为主要特征",
        "body_shape": "胖瘦均见",
        "common_signs": {
            "skin":           "肤色晦暗，色素沉着，容易出现瘀斑",
            "lips":           "口唇色暗",
            "tongue":         "舌暗或有瘀点，舌下络脉紫暗或增粗",  # ← 关键舌象
            "pulse":          "脉涩",
        },
        "psychological": {
            "summary":     "易烦，健忘",
            "irritability":    2,  # 易烦 → 触发高亮
            "anxiety":          0,
            "overthinking":     0,
            "low_mood":         0,
            "sensitivity":      0,
            "cognitive_note":   "健忘——血瘀质区别于其他体质的标志性心理特征",
        },
        "disease_tendency": [
            "癥瘕（肿瘤）、痛证、血证",
            "消渴（糖尿病）",
            "心血管疾病：高血压、动脉粥样硬化、冠心病",
            "突发性耳聋、子宫内膜异位症",
        ],
        "environment": "不耐受寒邪",
    },

    8: {  # 气郁质
        "name": "气郁质",
        "english": "Qi Stagnation",
        "category": "biased",
        "key_features": "气机郁滞，以神情抑郁、忧虑脆弱为主要特征",
        "body_shape": "形体瘦者为多",
        "common_signs": {
            "expression":     "神情抑郁，情感脆弱，烦闷不乐",
            "tongue":         "舌淡红，苔薄白",            # ← 关键舌象
            "pulse":          "脉弦",
        },
        "psychological": {
            "summary":     "性格内向不稳定、敏感多虑",
            "irritability":    1,
            "anxiety":          3,  # 焦虑、精神紧张
            "overthinking":     3,  # 敏感多虑
            "low_mood":         3,  # 闷闷不乐、情绪低沉
            "sensitivity":      3,  # 感情脆弱
        },
        "disease_tendency": [
            "脏躁、梅核气、百合病",
            "郁证（抑郁症）",
            "瘿瘤（甲状腺结节）",
            "不寐（失眠）",
            "认知衰弱",
        ],
        "environment": "对精神刺激适应能力较差；不适应阴雨天气",
    },

    9: {  # 特禀质
        "name": "特禀质",
        "english": "Allergic (Special)",
        "category": "biased",
        "key_features": "禀赋不耐，以过敏反应为主要特征",
        "body_shape": "一般无特殊",
        "common_signs": {
            "allergy":        "常见哮喘、风团、咽痒、鼻塞、喷嚏等",
            "tongue":         "多见齿痕舌",               # ← 关键舌象
            "pulse":          "因体质而异",
        },
        "psychological": {
            "summary":     "容易伴随焦虑、紧张",
            "irritability":    1,
            "anxiety":          2,  # 伴随焦虑
            "overthinking":     1,
            "low_mood":         1,
            "sensitivity":      2,  # 伴随紧张
        },
        "disease_tendency": [
            "哮喘、荨麻疹",
            "花粉症及药物过敏",
        ],
        "environment": "适应能力差，对易致敏季节适应能力差，易引发宿疾",
    },
}


# ==================== 舌象 → 体质映射（含置信度权重） ====================
# 基于国标第 4 节"常见表现"中舌象描述与现有 4 维检测输出的对应关系
# 权重：3=直接命中核心舌象特征，2=强关联，1=可能关联
#
# 每条规则格式：(舌色, 苔色, 裂纹状态, 齿痕状态) → [(体质ID, 权重), ...]
#   舌色:   red(淡红) / white(淡白) / crimson(深红)
#   苔色:   white(白苔) / yellow(黄苔) / nocoated(无苔/剥落)
#   裂纹:   crack(有裂纹) / normal(无裂纹)
#   齿痕:   indentation(有齿痕) / normal(无齿痕)
#
# 注意：当前模型只检测 4 维（舌色、苔色、裂纹、齿痕），
# 不包含"苔质"(薄/厚/腻)和"舌体"(胖大/瘦小)，因此映射存在局限性。
# 痰湿质的核心舌象"苔腻+舌胖大"只能通过"无裂纹+有苔+有齿痕"间接推断。

TONGUE_TO_CONSTITUTION = [
    # (舌色, 苔色, 裂纹状态, 齿痕状态) → [(体质ID, 权重), ...]
    # 权重: 3=核心命中, 2=强关联, 1=可能关联
    #
    # 痰湿质(5)映射逻辑:
    #   痰湿核心舌象 = 苔腻 + 舌胖大。当前模型缺"苔质"和"舌体"检测，但:
    #   - 苔腻在白苔/黄苔中均可出现 (白腻=寒痰, 黄腻=痰湿化热)
    #   - 舌胖大压迫牙齿常形成齿痕
    #   因此: 无裂纹(实证少裂纹) + 有苔(白/黄) + 有齿痕(胖大) → 痰湿质 ★★
    #         无裂纹 + 有苔(白/黄) → 痰湿质 ★
    #   有裂纹或无苔的组合中痰湿质不出现(裂纹→阴虚, 无苔→阴虚/气虚)
    # ————— 舌淡红 (red) —————
    ("red",    "white",    "normal", "normal",       [(1, 3), (8, 2), (5, 1)]),                   # 平和质/气郁质/可能痰湿
    ("red",    "white",    "normal", "indentation",  [(2, 3), (3, 2), (5, 2)]),                   # 气虚/阳虚/痰湿(白苔+齿痕)
    ("red",    "white",    "crack",  "normal",       [(4, 1), (2, 1)]),                           # 可能阴虚/气虚
    ("red",    "white",    "crack",  "indentation",  [(2, 3), (3, 1)]),                           # 气虚质为主
    ("red",    "yellow",   "normal", "normal",       [(6, 1), (8, 1), (5, 1)]),                   # 湿热/气郁/可能痰湿化热
    ("red",    "yellow",   "normal", "indentation",  [(6, 2), (5, 2), (2, 1)]),                   # 湿热/痰湿化热(黄苔+齿痕)
    ("red",    "yellow",   "crack",  "normal",       [(4, 2), (6, 2)]),                           # 阴虚+湿热
    ("red",    "yellow",   "crack",  "indentation",  [(2, 2), (6, 1), (4, 1)]),                   # 气虚为主
    ("red",    "nocoated", "normal", "normal",       [(4, 2), (8, 1)]),                           # 阴虚/气郁
    ("red",    "nocoated", "normal", "indentation",  [(2, 2), (4, 1)]),                           # 气虚+阴虚
    ("red",    "nocoated", "crack",  "normal",       [(4, 3)]),                                    # 阴虚质(核心)
    ("red",    "nocoated", "crack",  "indentation",  [(4, 2), (2, 2)]),                           # 阴虚+气虚
    # ————— 淡白舌 (white) —————
    ("white",  "white",    "normal", "normal",       [(3, 2), (2, 2), (5, 1), (8, 1)]),           # 阳虚/气虚/可能痰湿
    ("white",  "white",    "normal", "indentation",  [(2, 3), (3, 3), (5, 2), (9, 1)]),           # 气虚/阳虚/痰湿(白苔+齿痕)
    ("white",  "white",    "crack",  "normal",       [(2, 2), (3, 1), (4, 1)]),                   # 偏气虚
    ("white",  "white",    "crack",  "indentation",  [(2, 3), (3, 2)]),                           # 气虚+阳虚
    ("white",  "yellow",   "normal", "normal",       [(6, 1), (3, 1), (5, 1)]),                   # 湿热/阳虚/可能痰湿化热
    ("white",  "yellow",   "normal", "indentation",  [(3, 2), (5, 2), (2, 1)]),                   # 阳虚/痰湿化热(黄苔+齿痕)
    ("white",  "yellow",   "crack",  "normal",       [(4, 1), (2, 1)]),                           # 不典型
    ("white",  "yellow",   "crack",  "indentation",  [(2, 2), (3, 1)]),                           # 气虚为主
    ("white",  "nocoated", "normal", "normal",       [(3, 2), (2, 1)]),                           # 阳虚倾向
    ("white",  "nocoated", "normal", "indentation",  [(2, 2), (3, 3)]),                           # 阳虚+气虚
    ("white",  "nocoated", "crack",  "normal",       [(4, 2), (3, 1)]),                           # 阴虚+阳虚混合
    ("white",  "nocoated", "crack",  "indentation",  [(2, 3), (3, 2), (4, 1)]),                   # 气虚+阳虚+阴虚
    # ————— 深红舌 (crimson) —————
    ("crimson","white",    "normal", "normal",       [(6, 1), (7, 1)]),                           # 湿热/血瘀
    ("crimson","white",    "normal", "indentation",  [(6, 1), (2, 1)]),                           # 湿热+气虚
    ("crimson","white",    "crack",  "normal",       [(4, 2), (7, 1)]),                           # 阴虚/血瘀
    ("crimson","white",    "crack",  "indentation",  [(4, 2), (2, 1)]),                           # 阴虚为主
    ("crimson","yellow",   "normal", "normal",       [(6, 3), (5, 1), (4, 1)]),                   # 湿热(核心)/可能痰湿化热
    ("crimson","yellow",   "normal", "indentation",  [(6, 3), (5, 1), (2, 1)]),                   # 湿热(核心)/可能痰湿化热
    ("crimson","yellow",   "crack",  "normal",       [(6, 2), (4, 2), (7, 1)]),                   # 湿热+阴虚
    ("crimson","yellow",   "crack",  "indentation",  [(4, 2), (6, 2)]),                           # 阴虚+湿热
    ("crimson","nocoated", "normal", "normal",       [(4, 2), (6, 1), (7, 1)]),                   # 阴虚/湿热/血瘀
    ("crimson","nocoated", "normal", "indentation",  [(4, 2), (2, 1)]),                           # 阴虚为主
    ("crimson","nocoated", "crack",  "normal",       [(4, 3), (7, 2)]),                           # 阴虚质(核心)/血瘀
    ("crimson","nocoated", "crack",  "indentation",  [(4, 3), (7, 2), (2, 1)]),                   # 阴虚(核心)/血瘀
]


# ==================== 附录 A：中医体质分类与判定量表 (共 26 题) ====================
# 每题 5 级 Likert 量表：1=没有/根本不 → 5=总是/非常
#
# 题目结构: (key, 题目文本, 是否逆向计分)
#   正向题：得分 = 所选等级值 (1-5)，分越高越倾向该体质
#   逆向题：得分 = 6 - 所选等级值，仅平和质量表使用，用于衡量"偏离平和"的程度
#
# 性别限定题：湿热质含 2 道分性别题目
#   ("key", "题目", False, "female") — 仅女性回答
#   ("key", "题目", False, "male")   — 仅男性回答

SCALE_QUESTIONS = {
    # ——— 平和质 (4题) ———
    "balance": {
        "name": "平和质",
        "questions": [
            ("energy_good",        "您精力充沛吗?",          False),  # False=正向计分
            ("easy_fatigue",       "您容易疲乏吗?",          True),   # True=逆向计分 (1→5..5→1)
            ("low_mood",           "您感到闷闷不乐、情绪低沉吗?", True),
            ("cold_intolerant",    "您比一般人耐受不了寒冷吗?",  True),
        ],
        "note": "逆向计分条目标*，即：1→5,2→4,3→3,4→2,5→1",
    },

    # ——— 气虚质 (3题) ———
    "qi_deficiency": {
        "name": "气虚质",
        "questions": [
            ("easy_fatigue",       "您容易疲乏吗?",          False),
            ("short_breath",       "您容易气短(呼吸短促，接不上气)吗?", False),
            ("palpitations",       "您容易心慌吗?",          False),
        ],
    },

    # ——— 阳虚质 (3题) ———
    "yang_deficiency": {
        "name": "阳虚质",
        "questions": [
            ("stomach_cold",       "您胃脘部、背部或腰膝部怕冷吗?", False),
            ("more_clothes",       "您感到怕冷、衣服比别人穿得多吗?", False),
            ("cold_intolerant",    "您比一般人耐受不了寒冷吗?",  False),
        ],
    },

    # ——— 阴虚质 (3题) ———
    "yin_deficiency": {
        "name": "阴虚质",
        "questions": [
            ("body_heat",          "您感觉身体、脸上发热吗?",  False),
            ("dry_skin_lips",      "您皮肤或口唇干吗?",       False),
            ("cheek_red",          "您面部两颧潮红或偏红吗?",  False),
        ],
    },

    # ——— 痰湿质 (3题) ———
    "phlegm_damp": {
        "name": "痰湿质",
        "questions": [
            ("body_heavy",         "您感到身体沉重不轻松或不爽快吗?", False),
            ("belly_fat",          "您腹部肥满松软吗?",       False),
            ("sticky_mouth",       "您嘴里有黏黏的感觉吗?",    False),
        ],
    },

    # ——— 湿热质 (4题，含男女分题) ———
    "damp_heat": {
        "name": "湿热质",
        "questions": [
            ("oily_face",          "您面部或鼻部有油腻感或者油亮发光吗?", False),
            ("hot_urine",          "您小便时尿道有发热感、尿色浓(深)吗?", False),
            ("yellow_discharge",   "您带下色黄(白带颜色发黄)吗?",    False, "female"),  # 限女性
            ("scrotal_wet",        "您的阴囊部位潮湿吗?",      False, "male"),    # 限男性
        ],
    },

    # ——— 血瘀质 (3题) ———
    "blood_stasis": {
        "name": "血瘀质",
        "questions": [
            ("body_pain",          "您身体上有哪里疼痛吗?",    False),
            ("dull_complexion",    "您面色晦暗或容易出现褐斑吗?", False),
            ("dark_lips",          "您口唇颜色偏暗吗?",       False),
        ],
    },

    # ——— 气郁质 (3题) ———
    "qi_stagnation": {
        "name": "气郁质",
        "questions": [
            ("low_mood",           "您感到闷闷不乐、情绪低沉吗?",  False),
            ("nervous_anxious",    "您容易精神紧张、焦虑不安吗?",  False),
            ("sensitive_fragile",  "您多愁善感、感情脆弱吗?",   False),
        ],
    },

    # ——— 特禀质 (4题) ———
    "allergic": {
        "name": "特禀质",
        "questions": [
            ("sneeze_no_cold",     "您没有感冒时也会打喷嚏吗?", False),
            ("allergy_prone",      "您容易过敏(药物、食物、气味、花粉等)吗?", False),
            ("urticaria",          "您的皮肤容易起荨麻疹(风团、风疹块、风疙瘩)吗?", False),
            ("skin_scratch_red",   "您的皮肤一抓就红，并出现抓痕吗?", False),
        ],
    },
}

# ==================== 判定算法 (Section 5) ====================
# 依据 GB/T 46939—2025 第 5 节"判定方法"
#
# 核心流程：
#   1. 计算各亚量表原始分（每题 1-5 分求和，逆向题先翻转）
#   2. 原始分 → 转化分：归一化到 0-100 区间
#   3. 判定阈值：
#      平和质：转化分 ≥ 60 且 8 项偏颇均 < 30 → "是"
#              转化分 ≥ 60 且 8 项偏颇均 < 40 → "基本是"
#              否则 → "否"
#      偏颇质：转化分 ≥ 40 → "是"
#              30 ≤ 转化分 ≤ 39 → "倾向是"
#              转化分 < 30 → "否"

def calc_transformed_score(raw_score, question_count):
    """
    原始分转转化分
    公式: [(原始分 - 条目数) / (条目数 × 4)] × 100
    Args:
        raw_score: 各题得分之和 (每题为 1-5 分)
        question_count: 条目数
    Returns:
        转化分 (0-100)
    """
    return ((raw_score - question_count) / (question_count * 4)) * 100


def judge_constitution(scores: dict) -> dict:
    """
    根据 9 种体质的转化分判定体质类型

    Args:
        scores: {"balance": 65, "qi_deficiency": 25, ...}  9 种体质的转化分

    Returns:
        {
            "primary": "气虚质",           # 主体质
            "primary_confidence": "是",     # 是/倾向是
            "balance_status": "基本是",     # 是/基本是/否
            "all_results": {...}           # 全部判定明细
        }
    """
    balance_score = scores.get("balance", 0)
    biased_scores = {
        k: v for k, v in scores.items() if k != "balance"
    }

    # 平和质判定
    if balance_score >= 60 and all(v < 30 for v in biased_scores.values()):
        balance_status = "是"
    elif balance_score >= 60 and all(v < 40 for v in biased_scores.values()):
        balance_status = "基本是"
    else:
        balance_status = "否"

    # 偏颇体质判定
    results = {}
    for key, value in biased_scores.items():
        if value >= 40:
            results[key] = "是"
        elif 30 <= value <= 39:
            results[key] = "倾向是"
        else:
            results[key] = "否"

    # 找出主体质：在偏颇体质中选转化分最高且 ≥30 的
    # 注意：即使有偏颇质 ≥30，若平和质判定为"是"，主体质仍为平和质
    primary_type = None
    primary_confidence = "否"
    max_score = 0
    for key, value in biased_scores.items():
        if value >= 30 and value > max_score:
            max_score = value
            primary_type = key
            primary_confidence = results.get(key, "否")

    # 平和质优先级最高：一旦判定为"是"，直接覆盖偏颇质结果
    if balance_status == "是":
        primary_type = "balance"
        primary_confidence = "是"

    # 将字符串 key 转为中文体质名
    if primary_type is None:
        primary_display = "无法判定"
    elif primary_type == "balance":
        primary_display = "平和质"
    else:
        cid = SCALE_KEY_TO_ID.get(primary_type)
        primary_display = CONSTITUTION_TYPES.get(cid, primary_type) if cid else primary_type

    result = {
        "primary": primary_display,
        "primary_confidence": primary_confidence,
        "balance_status": balance_status,
        "scores": scores,
        "biased_results": results,
    }
    return result


# ==================== 快捷体质 ID→名称 映射 ====================

CONSTITUTION_ID_NAME = {
    1: "平和质", 2: "气虚质", 3: "阳虚质",
    4: "阴虚质", 5: "痰湿质", 6: "湿热质",
    7: "血瘀质", 8: "气郁质", 9: "特禀质",
}

SCALE_KEY_TO_ID = {
    "balance": 1, "qi_deficiency": 2, "yang_deficiency": 3,
    "yin_deficiency": 4, "phlegm_damp": 5, "damp_heat": 6,
    "blood_stasis": 7, "qi_stagnation": 8, "allergic": 9,
}


# ==================== 身心分析输出结构 (供 GUI 使用) ====================

def get_body_mind_report(constitution_id: int) -> dict:
    """
    函数作用:
        根据体质 ID 从 CONSTITUTION 数据库中提取完整数据，生成面向用户的身心评估报告。
        报告涵盖: 身体信号 / 心理特征 / 国标舌象 / 疾病风险 / 环境适应 / 调理建议。

    输入:
        constitution_id: 体质 ID (1-9)，对应:
            1=平和质, 2=气虚质, 3=阳虚质, 4=阴虚质, 5=痰湿质,
            6=湿热质, 7=血瘀质, 8=气郁质, 9=特禀质

    输出:
        dict: {
            "constitution_name":   str  体质中文名,
            "constitution_english": str 英文名,
            "category":            str  "正常体质" | "偏颇体质",
            "one_line":            str  一句话核心特征,
            "body_signals":        [str] 身体信号列表 (排除舌象和脉象, 后者属于专业诊断),
            "tongue":              str  国标舌象描述 (如 "舌淡红，舌边有齿痕"),
            "mind_summary":        str  心理特征一句话总结,
            "mind_highlights":     [str] 高风险心理维度 (如 ["易怒/烦躁","焦虑/紧张"]),
            "disease_risk":        [str] 易患疾病列表,
            "environment_note":    str  环境适应能力描述,
            "advice":              {diet, exercise, lifestyle, mind}  四方面调理建议,
        }

    执行流程:
        1. 从 CONSTITUTION 字典中按 ID 获取体质原始数据
        2. 提取身体信号 (排除 tongue/pulse — 属于中医专业诊断而非用户自评)
        3. 提取心理特征:
           - mind_summary: 性格描述原文
           - mind_highlights: 量化维度 >= 2 的标记为"需关注"
             (irritability/anxiety/overthinking/low_mood/sensitivity 共 5 个维度, 各 0-3 分)
        4. 调用 get_constitution_advice(id) 获取饮食/运动/生活/心理四方面建议
        5. 组装并返回完整报告字典
    """
    c = CONSTITUTION.get(constitution_id)
    if not c:
        return {"error": f"未知体质ID: {constitution_id}"}

    psych = c["psychological"]

    # 构建可读结论
    body_signals = []
    for k, v in c["common_signs"].items():
        if k not in ("tongue", "pulse") and v:
            body_signals.append(v)

    mind_summary = psych["summary"]

    # 心理高亮项
    mind_highlights = []
    if psych.get("irritability", 0) >= 2:
        mind_highlights.append("易怒/烦躁")
    if psych.get("anxiety", 0) >= 2:
        mind_highlights.append("焦虑/紧张")
    if psych.get("overthinking", 0) >= 2:
        mind_highlights.append("思虑过度")
    if psych.get("low_mood", 0) >= 2:
        mind_highlights.append("情绪低落")
    if psych.get("sensitivity", 0) >= 2:
        mind_highlights.append("敏感多疑")

    return {
        "constitution_name": c["name"],
        "constitution_english": c["english"],
        "category": "正常体质" if c["category"] == "normal" else "偏颇体质",
        "one_line": c["key_features"],
        "body_signals": body_signals,
        "tongue": c["common_signs"].get("tongue", ""),
        "mind_summary": mind_summary,
        "mind_highlights": mind_highlights,
        "disease_risk": c["disease_tendency"],
        "environment_note": c["environment"],
        "advice": get_constitution_advice(constitution_id),
    }


def get_constitution_advice(constitution_id: int) -> dict:
    """按体质给出的调理建议"""
    advice_map = {
        1: {  # 平和质
            "diet": "均衡饮食，不宜偏嗜",
            "exercise": "规律运动，保持每周3-5次，每次30分钟以上",
            "lifestyle": "保持充足睡眠，劳逸结合",
            "mind": "保持乐观心态，适度社交",
        },
        2: {  # 气虚质
            "diet": "多食益气健脾食物：山药、莲子、大枣、小米、黄芪炖鸡；少食耗气食物：萝卜、槟榔",
            "exercise": "温和运动：太极拳、八段锦、散步；避免剧烈运动和大量出汗",
            "lifestyle": "避免久卧伤气，适当晒太阳补阳气",
            "mind": "多与人交流，避免独处思虑过度",
        },
        3: {  # 阳虚质
            "diet": "多食温阳食物：羊肉、韭菜、生姜、桂圆、核桃；忌生冷寒凉",
            "exercise": "运动宜在上午阳气盛时，散步、慢跑、太极拳",
            "lifestyle": "注意保暖，尤其腰腹和下肢，睡前热水泡脚",
            "mind": "多听振奋音乐，避免沉静孤独",
        },
        4: {  # 阴虚质
            "diet": "多食滋阴食物：银耳、百合、梨、鸭肉、枸杞；忌辛辣燥热",
            "exercise": "避免大量出汗的运动，适合游泳、瑜伽",
            "lifestyle": "保证充足睡眠，避免熬夜（熬夜最伤阴）",
            "mind": "克制急躁，练习冥想或腹式呼吸",
        },
        5: {  # 痰湿质
            "diet": "清淡饮食，多食薏米、冬瓜、赤小豆、陈皮；忌肥甘厚腻、甜食",
            "exercise": "加强运动排湿：跑步、游泳、球类，每周至少5次",
            "lifestyle": "避免潮湿环境居住，衣着透气",
            "mind": "主动社交，避免懒散消沉",
        },
        6: {  # 湿热质
            "diet": "清热祛湿：绿豆、苦瓜、薏米、菊花茶；忌辛辣油腻、戒烟限酒",
            "exercise": "高强度运动利于排汗祛湿：跑步、健身、球类",
            "lifestyle": "保持皮肤清洁，穿着透气，避免潮湿闷热环境",
            "mind": "克制烦躁情绪，学会深呼吸放松",
        },
        7: {  # 血瘀质
            "diet": "活血化瘀：山楂、黑木耳、醋、玫瑰花茶、少量红酒；忌寒凉",
            "exercise": "坚持运动促进循环：快走、舞蹈、太极拳",
            "lifestyle": "避免久坐久卧，注意保暖防寒",
            "mind": "多参加集体活动，避免独处思闷",
        },
        8: {  # 气郁质
            "diet": "理气解郁：玫瑰花茶、柑橘、佛手、金橘、黄花菜",
            "exercise": "户外运动接触阳光：跑步、登山、骑行",
            "lifestyle": "规律作息，多参加群体活动，培养兴趣爱好",
            "mind": "主动倾诉，建议学习正念或心理咨询",
        },
        9: {  # 特禀质
            "diet": "避免过敏食物，饮食清淡；增强免疫：黄芪、党参煲汤",
            "exercise": "适度锻炼增强体质，注意避免花粉季节户外运动",
            "lifestyle": "保持环境清洁通风，常备抗过敏药物",
            "mind": "避免因过敏反复而焦虑，保持平和心态",
        },
    }
    return advice_map.get(constitution_id, {})


# ==================== 四维舌象 → 体质推测 (核心算法) ====================
# 这是舌诊模块的核心推理引擎。
# 输入 4 维检测结果（舌色、苔色、裂纹、齿痕），在 TONGUE_TO_CONSTITUTION
# 规则表中查找匹配项，返回候选体质列表（按权重降序）。
#
# 调用方通常取 top-N 或结合问诊量表综合判定。

def infer_constitution_from_tongue(color_class: str, coated_class: str,
                                     crack_class: str, indent_class: str) -> list:
    """
    函数作用:
        将 4 维舌象检测结果映射为候选体质列表。这是舌诊推理链路的最终判定环节。
        判定方式: 在 36 条硬编码规则表 (TONGUE_TO_CONSTITUTION) 中查找匹配。

    输入:
        color_class:  舌色分类 — "red"(淡红) / "white"(淡白) / "crimson"(深红)
        coated_class: 苔色分类 — "white"(白苔) / "yellow"(黄苔) / "nocoated"(无苔)
        crack_class:  裂纹状态 — "crack"(有裂纹) / "normal"(无裂纹)
        indent_class: 齿痕状态 — "indentation"(有齿痕) / "normal"(无齿痕)

    输出:
        候选体质列表，每项包含 id, name, english, weight, category，按 weight 降序排列。
        例: [{"id":2, "name":"气虚质", "english":"Qi Deficiency", "weight":3, "category":"biased"},
              {"id":3, "name":"阳虚质", "english":"Yang Deficiency", "weight":1, "category":"biased"}]
        无匹配时返回空列表 (理论上不会发生，因为 3×3×2×2=36 种组合全覆盖)

    执行流程:
        1. 拼接 4 个参数为元组 key = (color, coated, crack, indent)
           例: ("red", "white", "crack", "indentation")
        2. 在 TONGUE_TO_CONSTITUTION (36 条规则) 中遍历查找:
           - 每条规则前 4 个元素是匹配条件
           - 第 5 个元素是该组合对应的 [(体质ID, 权重), ...] 列表
           - 找到第一条匹配即停止 (规则表无重复)
        3. 对匹配到的候选人按 weight 降序排序
        4. 为每个候选人填充完整信息 (name / english / category)
        5. 返回结果列表
    """
    key = (color_class, coated_class, crack_class, indent_class)
    candidates = None
    for entry in TONGUE_TO_CONSTITUTION:
        if entry[:4] == key:
            candidates = entry[4]
            break

    if not candidates:
        return []

    # 按权重排序，返回完整信息
    result = []
    for cid, weight in sorted(candidates, key=lambda x: -x[1]):
        result.append({
            "id": cid,
            "name": CONSTITUTION_TYPES.get(cid, f"未知{cid}"),
            "english": CONSTITUTION_EN.get(cid, ""),
            "weight": weight,
            "category": CONSTITUTION[cid]["category"] if cid in CONSTITUTION else "unknown",
        })
    return result

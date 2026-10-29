"""测试共享数据。"""

SUCCESS_INSTRUCTION = (
    "配置 0.1mol/L 的乙酸乙酯溶液，取 2ml，升温到 45℃，搅拌 30 分钟，"
    "然后取样检测，若产率低于 60%，则重新调整温度到 50℃再重复一轮；"
    "如果产率≥60%，实验结束，生成实验记录。"
)

# 阈值 80%：45℃(55%) 与 50℃(65%) 都不达标 -> 到达轮次上限
STRICT_INSTRUCTION = SUCCESS_INSTRUCTION.replace("产率低于 60%", "产率低于 80%")

# 体积 500ml 超出白名单范围 -> 调用工具前拦截
BAD_VOLUME_INSTRUCTION = SUCCESS_INSTRUCTION.replace("取 2ml", "取 500ml")

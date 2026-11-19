"""测试环境兜底：llm_invoke 在导入期要求 DEEPSEEK_API_KEY。"""

import os

os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")

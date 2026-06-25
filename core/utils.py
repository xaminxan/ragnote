"""共享工具函数"""
import time

# 简单的速率限制：记录上次调用时间
_last_llm_call_time = 0.0
_MIN_CALL_INTERVAL = 0.5  # 最小调用间隔（秒）


def safe_llm_call(llm, messages, fallback, max_retries=2, label="LLM"):
    """带重试的LLM调用，失败时返回fallback而非抛异常"""
    global _last_llm_call_time

    # 速率限制：确保两次调用之间有最小间隔
    elapsed = time.time() - _last_llm_call_time
    if elapsed < _MIN_CALL_INTERVAL:
        time.sleep(_MIN_CALL_INTERVAL - elapsed)

    for attempt in range(max_retries + 1):
        try:
            response = llm.invoke(messages)
            _last_llm_call_time = time.time()
            return response.content
        except Exception as e:
            err_msg = str(e)[:60]
            if attempt < max_retries:
                print(f"⚠️ {label}失败(重试{attempt+1}/{max_retries}): {err_msg}")
                time.sleep(2 * (attempt + 1))
            else:
                print(f"⚠️ {label}最终失败: {err_msg}，使用备选内容")
                _last_llm_call_time = time.time()
                return fallback

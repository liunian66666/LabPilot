import asyncio
from contextlib import asynccontextmanager


@asynccontextmanager
async def async_timer():
    start = asyncio.get_running_loop().time()
    try:
        print("开始")
        yield  # 这里可以yield任何需要传出去的对象
    except Exception as e:
        # 这里接住块里抛的异常，就不会再往外层抛了
        print(f"捕获到业务异常: {e}")
    finally:
        cost = asyncio.get_running_loop().time() - start
        print("结束")
        print(f"这段异步代码运行耗时: {cost:.2f} 秒")

# 使用方式
async def main():
    async with async_timer():
        await asyncio.sleep(2)  # 模拟异步业务逻辑
        raise Exception("这是一个异常")

if __name__ == "__main__":
    asyncio.run(main())

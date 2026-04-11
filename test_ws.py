import asyncio
import websockets


async def test():
    async with websockets.connect(
        "ws://localhost:9090/asr",
        ping_interval=None,
        ping_timeout=None,
        close_timeout=None,
        max_size=None,
    ) as ws:
        print("Connected!")
        config = await ws.recv()
        print("Config:", config)

        chunk = bytes(16000 * 2)  # 1 second of audio
        for i in range(10000):
            try:
                await ws.send(chunk)
                if i % 1000 == 0:
                    print(f"Sent chunk {i}")
            except Exception as e:
                print(f"Error at chunk {i}: {e}")
                break
        print("Finished sending!")


asyncio.run(test())

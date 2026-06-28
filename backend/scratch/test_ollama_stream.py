import asyncio
import httpx
from backend.modules.agent import providers as P


async def main():
    async def on_delta(reasoning: str, content: str) -> None:
        print(f"[DELTA] reasoning={repr(reasoning)} content={repr(content)}")

    info = P.provider_for("ollama")
    endpoint = "http://localhost:11434"
    model = "gemma4:e2b"
    messages = [{"role": "user", "content": "think about the number 42 and say hello"}]
    tools = []

    async with httpx.AsyncClient(timeout=30) as client:
        result = await P.chat_stream(
            client,
            info,
            endpoint,
            model,
            messages,
            tools,
            on_delta,
        )
    print("\n--- FINAL RESULT ---")
    print(f"Content: {repr(result.content)}")
    print(f"Assistant Message: {result.assistant_message}")


if __name__ == "__main__":
    asyncio.run(main())

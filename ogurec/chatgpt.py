import os
from collections.abc import AsyncIterator

# pydantic-ai при первом запуске печатает в лог ASCII-баннер с рекламой Logfire
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, SystemPromptPart, TextPart, UserPromptPart
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.output import PromptedOutput
from pydantic_ai.providers.openai import OpenAIProvider

from ogurec.config.settings import Settings


class GPTClientError(Exception):
    pass


class RateLimitError(GPTClientError):
    """Ошибка превышения лимита запросов (429)."""


# freellmapi отвечает 413/404, когда у всех провайдеров кончился лимит или контекст
RATE_LIMIT_STATUSES = {429, 413, 404}


def to_model_messages(messages: list[dict]) -> list[ModelMessage]:
    """История в формате OpenAI (role/content) -> история pydantic-ai."""
    result: list[ModelMessage] = []
    for msg in messages:
        role, content = msg.get("role"), msg.get("content") or ""
        if role == "assistant":
            result.append(ModelResponse(parts=[TextPart(content)]))
            continue

        if role == "system":
            part = SystemPromptPart(content)
        else:
            # поле name pydantic-ai не передает — без него все реплики людей слились бы в одного
            name = msg.get("name")
            part = UserPromptPart(f"{name}: {content}" if name else content)

        # подряд идущие system/user — это один запрос к модели
        if result and isinstance(result[-1], ModelRequest):
            result[-1].parts.append(part)
        else:
            result.append(ModelRequest(parts=[part]))
    return result


def _wrap_error(e: Exception) -> GPTClientError:
    if isinstance(e, ModelHTTPError):
        if e.status_code in RATE_LIMIT_STATUSES:
            return RateLimitError(f"API rate limit exceeded ({e.status_code}): {e.body}")
        return GPTClientError(f"API error {e.status_code}: {e.body}")
    # freellmapi иногда шлет ошибку внутри стрима, без HTTP-статуса
    if "429" in str(e) or "rate" in str(e).lower():
        return RateLimitError(f"API rate limit in stream: {e}")
    return GPTClientError(f"API error: {e}")


class GPTClient:
    def __init__(self, api_key: str, settings: Settings):
        self.settings = settings
        # ретраи держит наш код (на 429 он режет историю), ретраи SDK поверх них только тянут время
        client = AsyncOpenAI(
            base_url=settings.api_base_url.removesuffix("/chat/completions"),
            api_key=api_key,
            max_retries=0,
        )
        self.provider = OpenAIProvider(openai_client=client)
        self.agent = Agent(output_type=str)
        self.last_model: str | None = None

    def model(self, name: str | None = None) -> OpenAIChatModel:
        return OpenAIChatModel(name or self.settings.llm_model, provider=self.provider)

    async def chat_completion(
        self,
        messages: list[dict],
        temperature: float = 1.0,
        max_tokens: int = 2048,
        top_p: float = 1.0,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        model_settings = OpenAIChatModelSettings(
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            openai_reasoning_effort="none",  # OpenAI o1/o3/gpt5, Groq, Cerebras
        )

        self.last_model = None
        try:
            async with self.agent.run_stream(
                message_history=to_model_messages(messages),
                model=self.model(model),
                model_settings=model_settings,
            ) as result:
                async for text in result.stream_text(delta=True, debounce_by=None):
                    if text:
                        yield text
                self.last_model = result.response.model_name
        except GPTClientError:
            raise
        except Exception as e:
            raise _wrap_error(e) from e

    async def structured[T](
        self,
        system: str,
        prompt: str,
        output_type: type[T],
        model: str | None = None,
        temperature: float = 0,
        max_tokens: int = 256,
    ) -> T:
        """Один запрос с ответом в виде pydantic-модели: pydantic-ai сам проверит и попросит поправить."""
        # схема уходит текстом в промпт, а не через tool calling: половина бесплатных провайдеров tools не умеет
        agent = Agent(output_type=PromptedOutput(output_type), system_prompt=system, retries=2)
        try:
            result = await agent.run(
                prompt,
                model=self.model(model),
                model_settings=OpenAIChatModelSettings(
                    temperature=temperature,
                    max_tokens=max_tokens,
                    openai_reasoning_effort="none",
                ),
            )
        except Exception as e:
            raise _wrap_error(e) from e
        self.last_model = result.response.model_name
        return result.output

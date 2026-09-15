from loguru import logger

DECISION_SYSTEM_PROMPT = (
    "Ты решаешь, стоит ли Discord-боту Ogurec влезать в разговор. Тебе дают последние сообщения чата.\n"
    "Ответь одним словом:\n"
    "reply — есть что сказать по теме: к нему обратились, спросили, идет живой разговор, где его реплика уместна.\n"
    "react — тема не его, но сообщение забавное/яркое: хватит реакции эмодзи.\n"
    "skip — разговор без него, люди говорят между собой, техническая переписка, поток коротких сообщений.\n"
    "По умолчанию skip: лучше промолчать, чем влезть не по делу. Отвечай строго одним словом."
)

ACTIONS = ("reply", "react", "skip")


async def decide_action(recent_lines: list[str], gpt_client, model: str = "auto:fast") -> str:
    """reply / react / skip — решение принимает дешевая модель, а не рандом."""
    if gpt_client is None or not recent_lines:
        return "skip"

    messages = [
        {"role": "system", "content": DECISION_SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(recent_lines[-12:])[:2000]},
    ]
    try:
        result = ""
        async for chunk in gpt_client.chat_completion(messages, temperature=0, max_tokens=5, model=model):
            result += chunk
    except Exception as e:
        logger.warning(f"decide_action error: {e}")
        return "skip"

    word = result.strip().lower()
    for action in ACTIONS:
        if action in word:
            logger.info(f"decide_action -> {action}")
            return action
    return "skip"

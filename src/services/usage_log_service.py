from datetime import datetime
from pathlib import Path
from threading import Lock

from app.paths import tokens_log_for_folder


_LOG_WRITE_LOCK = Lock()


def thinking_token_count(usage_metadata) -> int:
    thought_tokens = getattr(usage_metadata, "thoughts_token_count", 0) or 0
    if thought_tokens:
        return int(thought_tokens)

    total_tokens = getattr(usage_metadata, "total_token_count", 0) or 0
    prompt_tokens = getattr(usage_metadata, "prompt_token_count", 0) or 0
    output_tokens = getattr(usage_metadata, "candidates_token_count", 0) or 0
    inferred_tokens = total_tokens - prompt_tokens - output_tokens
    return max(int(inferred_tokens), 0)


def calculate_usage_cost(model_prices: dict, model_name: str, usage_metadata) -> float:
    in_tokens = getattr(usage_metadata, "prompt_token_count", None) or 0
    out_tokens = getattr(usage_metadata, "candidates_token_count", None) or 0
    thought_tokens = thinking_token_count(usage_metadata)
    prices = model_prices.get(model_name, (0.0, 0.0))
    return (in_tokens / 1_000_000 * prices[0]) + ((out_tokens + thought_tokens) / 1_000_000 * prices[1])


def append_usage_log(folder: str, model_prices: dict, model_name: str, usage_metadata) -> None:
    log_path = tokens_log_for_folder(folder)
    cost = calculate_usage_cost(model_prices, model_name, usage_metadata)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    thought_tokens = thinking_token_count(usage_metadata)
    log_line = (
        f"{now};{model_name};{getattr(usage_metadata, 'prompt_token_count', None) or 0};"
        f"{getattr(usage_metadata, 'candidates_token_count', None) or 0};{thought_tokens};{cost:.6f}\n"
    )

    with _LOG_WRITE_LOCK:
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(log_line)


def read_usage_log(folder: str) -> tuple[list[tuple[str, str, str, str, str]], float]:
    log_path = Path(tokens_log_for_folder(folder))
    row_data = []
    total_cost = 0.0

    with log_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            parts = line.strip().split(";")
            if len(parts) == 5:
                row_data.append(tuple(parts[:4] + ["0", parts[4]]))
                total_cost += float(parts[4])
            elif len(parts) == 6:
                row_data.append(tuple(parts))
                total_cost += float(parts[5])

    return row_data, total_cost

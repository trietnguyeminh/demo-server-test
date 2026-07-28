from __future__ import annotations

import argparse
import json

from server.config import Settings
from server.engine import build_engine
from server.models import BenchmarkRequest, SearchProfile
from server.service import SearchService


DEFAULT_QUERIES = [
    "người mặc áo đỏ đứng cạnh ô tô",
    "người đánh trống Yamaha ngoài trời",
    "xe máy có biển số bắt đầu bằng 59",
    "người đàn ông nói về thời tiết",
    "sau khi mở cửa người phụ nữ đặt túi lên bàn",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=["fast", "auto", "accurate"], default="auto")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--query", action="append", dest="queries")
    args = parser.parse_args()

    settings = Settings.from_env()
    service = SearchService(build_engine(settings), settings)
    result = service.benchmark(
        BenchmarkRequest(
            queries=args.queries or DEFAULT_QUERIES,
            profile=SearchProfile(args.profile),
            repeats=args.repeats,
            top_k=args.top_k,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

from app import create_app
import os
import sys

app = create_app()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    print(sys.version)
    # 本番では gunicorn を使用する。直接起動は開発用途を想定し、デバッガ
    # （任意コード実行に繋がりうる）は既定で無効。必要時のみ環境変数で有効化する。
    debug = _env_bool("DSTT_DEBUG", False)
    host = os.environ.get("DSTT_RUN_HOST", "0.0.0.0")
    port = int(os.environ.get("DSTT_RUN_PORT", "5000"))
    app.run(host=host, port=port, debug=debug)

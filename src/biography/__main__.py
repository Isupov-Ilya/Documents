from .cli import main
from .runtime import configure_stdio

configure_stdio()
raise SystemExit(main())

from contextlib import asynccontextmanager
from ipaddress import ip_address

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from ytu_scheduler.auth import require_owner
from ytu_scheduler.config import get_settings
from ytu_scheduler.database import create_schema
from ytu_scheduler.routers import catalog, imports, owner, releases, schedules, term_dates


@asynccontextmanager
async def lifespan(_: FastAPI):
    create_schema()
    yield


def create_app(*, local_owner: bool = False) -> FastAPI:
    settings = get_settings()
    application = FastAPI(title=settings.app_name, version="0.2.0", lifespan=lifespan)
    local_origins = {"http://localhost:3001", "http://127.0.0.1:3001"}
    application.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(local_origins) if local_owner else settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if local_owner:
        # Owner tools are a separate loopback service, never part of the released API.
        @application.middleware("http")
        async def loopback_only(request: Request, call_next):
            try:
                is_local = (
                    request.client is not None and ip_address(request.client.host).is_loopback
                )
            except ValueError:
                is_local = False
            host = request.headers.get("host", "")
            origin = request.headers.get("origin")
            if (
                not is_local
                or host not in {"localhost:8001", "127.0.0.1:8001"}
                or (origin is not None and origin not in local_origins)
            ):
                return JSONResponse({"detail": "Local owner access only"}, status_code=403)
            return await call_next(request)

        application.dependency_overrides[require_owner] = lambda: None
        application.include_router(owner.router)
        application.include_router(releases.router)
        application.include_router(term_dates.router)

    for source in (catalog.router, imports.router, schedules.router):
        if local_owner:
            application.include_router(source)
        else:
            student_router = APIRouter()
            student_router.routes = [
                route
                for route in source.routes
                if isinstance(route, APIRoute)
                and not any(dep.dependency is require_owner for dep in route.dependencies)
            ]
            application.include_router(student_router)

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()

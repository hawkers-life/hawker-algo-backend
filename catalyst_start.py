"""
catalyst_start.py — Catalyst AppSail startup wrapper.

Zoho Catalyst assigns a dynamic port via X_ZOHO_CATALYST_LISTEN_PORT.
This file reads that port and starts uvicorn on it automatically.

You do NOT need to edit this file.
"""
import os
import uvicorn

if __name__ == "__main__":
    # Catalyst provides port via X_ZOHO_CATALYST_LISTEN_PORT
    # Render/others use PORT
    port = int(
        os.environ.get("X_ZOHO_CATALYST_LISTEN_PORT") or
        os.environ.get("PORT") or
        8000
    )

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        workers=2,
        log_level="info",
    )

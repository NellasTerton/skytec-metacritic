"""Тот же зонд как Vercel-функция: GET / возвращает JSON, GET /md — markdown-таблицу."""
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from probe import run, to_markdown

app = FastAPI()


@app.get("/")
async def probe_json():
    return await run()


@app.get("/md", response_class=PlainTextResponse)
async def probe_md():
    return to_markdown(await run())

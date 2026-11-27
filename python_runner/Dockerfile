# The research agent's Python on the server (python_runner/server.mjs):
# Pyodide and the same packages as the browser (frontend/scripts/fetch-pyodide.mjs,
# checked file by file). Built from the repository root:
#   docker build -f python_runner/Dockerfile .
FROM node:20-alpine AS pyodide
COPY frontend/scripts/fetch-pyodide.mjs /fetch-pyodide.mjs
RUN node /fetch-pyodide.mjs /pyodide

# Noto Sans SC, so that figures can have Chinese text (fetch-font.mjs).
FROM node:20-alpine AS font
COPY python_runner/fetch-font.mjs /fetch-font.mjs
RUN node /fetch-font.mjs /fonts

FROM node:20-alpine
COPY --from=pyodide /pyodide /pyodide
COPY --from=font /fonts /fonts
COPY python_runner/server.mjs python_runner/run.cjs /app/
ENV PYODIDE_DIR=/pyodide/0.29.3/ FONT_FILE=/fonts/NotoSansSC-Regular.otf
USER node
EXPOSE 8000
CMD ["node", "/app/server.mjs"]

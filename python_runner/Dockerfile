# The research agent's Python on the server (python_runner/server.mjs):
# Pyodide and the same packages as the browser (frontend/scripts/fetch-pyodide.mjs,
# checked file by file). Built from the repository root:
#   docker build -f python_runner/Dockerfile .
FROM node:20-alpine AS pyodide
COPY frontend/scripts/fetch-pyodide.mjs /fetch-pyodide.mjs
RUN node /fetch-pyodide.mjs /pyodide

FROM node:20-alpine
COPY --from=pyodide /pyodide /pyodide
COPY python_runner/server.mjs python_runner/run.cjs /app/
ENV PYODIDE_DIR=/pyodide/0.29.3/
USER node
EXPOSE 8000
CMD ["node", "/app/server.mjs"]

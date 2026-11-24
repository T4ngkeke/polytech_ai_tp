# Stage 0: Python for the browser (Pyodide and the packages a course may
# import), served from /pyodide/<version>/ by this nginx: no CDN at run time. Its own
# stage so the download (~55 MB, checked file by file) stays cached.
FROM node:20-alpine AS pyodide
COPY frontend/scripts/fetch-pyodide.mjs /fetch-pyodide.mjs
RUN node /fetch-pyodide.mjs /pyodide

# Stage 1: Build the React application
FROM node:20-alpine AS builder

WORKDIR /app

# Copy package.json and install dependencies
COPY frontend/package*.json ./
RUN npm install

# Copy the rest of the frontend source code and build
COPY frontend/ ./
RUN npm run build

# Stage 2: Serve with Nginx
FROM nginx:alpine

# Copy the build output from the builder stage
COPY --from=builder /app/dist /usr/share/nginx/html
COPY --from=pyodide /pyodide /usr/share/nginx/html/pyodide

# Copy the custom Nginx configuration
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

# Expose port 80
EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]

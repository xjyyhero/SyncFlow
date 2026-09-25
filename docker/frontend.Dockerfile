FROM node:24-alpine
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build
# Local V1.0 deployment serves the built app; Vite keeps the API proxy configured.
CMD ["npm", "run", "preview", "--", "--host", "0.0.0.0", "--port", "5173"]

FROM node:24-alpine
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
CMD ["npm", "run", "dev"]

FROM node:20-alpine

WORKDIR /app

ENV NODE_ENV=development

COPY package.json package-lock.json* ./
RUN npm install --include=dev

COPY . .

ENV VITE_API_URL=${VITE_API_URL}

EXPOSE 3000

CMD ["npx", "vite", "--host", "0.0.0.0", "--port", "3000"]


# Containerizes wa-server without modifying server.js.
# The Playwright base image ships Node.js + all Chromium system dependencies,
# which whatsapp-web.js (puppeteer) needs to launch its bundled browser.
FROM mcr.microsoft.com/playwright:v1.49.0-noble

ENV NODE_ENV=production

WORKDIR /app

COPY package*.json ./
RUN npm install --omit=dev

COPY . .

EXPOSE 3001

CMD ["node", "server.js"]

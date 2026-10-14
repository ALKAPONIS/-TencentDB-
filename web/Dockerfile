# ---- build the eUI / Angular app
FROM node:22-slim AS build
WORKDIR /app
COPY package.json yarn.lock ./
RUN yarn install --frozen-lockfile --non-interactive --network-timeout 600000
COPY angular.json tsconfig.json .browserslistrc eslint.config.js ./
COPY src ./src
RUN yarn build

# ---- serve with nginx, proxying /api and /v1/chat/completions to the agent service
FROM nginx:1.27-alpine
ENV PORT=8080 \
    AGENT_URL=http://agent.railway.internal:8000 \
    NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1 \
    NGINX_ENVSUBST_FILTER="^(PORT|AGENT_URL|NGINX_LOCAL_RESOLVERS)$"
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist/browser /usr/share/nginx/html
EXPOSE 8080

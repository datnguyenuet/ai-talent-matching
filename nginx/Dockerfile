FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
# Remove default nginx config
RUN rm -f /etc/nginx/conf.d/default.conf.bak

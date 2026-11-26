# battle-lab: the page, the board socket and the season (server/app.py). Built by deploy/relay.sh
# from a context holding libaw (crates + data, to build awbw-replay) and this repo.
FROM rust:1.91-bookworm AS build
WORKDIR /src
COPY libaw/Cargo.toml libaw/Cargo.lock ./
COPY libaw/crates crates
COPY libaw/data data
RUN cargo build --release -p libaw-replay

FROM python:3.13-slim-bookworm
ENV PYTHONUNBUFFERED=1 AWBW_REPLAY_BIN=/usr/local/bin/awbw-replay LIBAW_WEB=/srv/app/vendor/libaw
WORKDIR /srv/app
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=build /src/target/release/awbw-replay /usr/local/bin/awbw-replay
COPY app/server server
COPY app/web web
COPY app/vendor vendor
CMD ["python3", "-I", "server/app.py"]

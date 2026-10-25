# Builds and runs the Clone Maker window on Linux under Xvfb (screenshots, self-test, resize benchmark).
FROM rust:1-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
        libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev libssl-dev libayatana-appindicator3-dev \
        build-essential xvfb imagemagick xdotool fontconfig \
    && rm -rf /var/lib/apt/lists/* \
    && rustup component add rustfmt clippy

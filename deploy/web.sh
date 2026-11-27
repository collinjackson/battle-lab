#!/bin/zsh
# Deploy Battle Lab's page to Vercel (project battle-lab): web/index.html, libaw's vendored viewer,
# and config.js naming the relay (Cloud Run). Static only; the dynamic parts are the relay's.
#   deploy/web.sh            (RELAY=https://... to override the Cloud Run URL)
set -euo pipefail
export CLOUDSDK_ACTIVE_CONFIG_NAME=${GCLOUD_CONFIG:-com-tower}
HERE=${0:A:h:h}
RELAY=${RELAY:-$(gcloud run services describe battle-lab --project ${PROJECT:-comtower} --region ${REGION:-us-central1} --format 'value(status.url)')}
[[ $RELAY == https://* ]] || { echo "no relay URL" >&2; exit 1; }
$HERE/tools/vendor_libaw.sh
WATER=$HERE/web/awbw-water.json   # AWBW sprite-sheet coordinates: made locally, never committed
[[ -f $WATER ]] || python3 -I $HERE/tools/awbw_water.py
OUT=$HERE/deploy/out/battle-lab          # kept between runs: it holds the .vercel link
mkdir -p $OUT; find $OUT -mindepth 1 -maxdepth 1 ! -name .vercel -exec rm -rf {} +
cp $HERE/web/index.html $HERE/web/awbw-water.json $HERE/web/sound.js $HERE/web/favicon.svg $OUT/
cp -R $HERE/vendor/libaw $OUT/libaw
mkdir -p $OUT/sfx && cp -R $HERE/web/sfx/cc0 $OUT/sfx/cc0   # the CC0 effects (web/sfx/cc0/README.md)
print -r -- "window.BATTLE_LAB_RELAY = '$RELAY';" > $OUT/config.js
cat > $OUT/vercel.json <<'JSON'
{ "headers": [
  { "source": "/(index.html|config.js)?", "headers": [{ "key": "Cache-Control", "value": "public, max-age=0, must-revalidate" }] }
] }
JSON
cd $OUT && vercel deploy --prod --yes

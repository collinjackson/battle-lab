#!/bin/zsh
# Deploy Battle Lab's relay to Cloud Run (PROJECT, default comtower; REGION, default us-central1) as `battle-lab`, public.
# The page itself is on Vercel (deploy/web.sh); the relay serves it too, as a backup address.
# Exactly one instance, CPU always on: it is the one poller AWBW sees, and its background tasks
# (Battleground's feed, the AWBW walker) must run between requests. The season log lives in
# gs://$BUCKET/battle-lab/.
#   deploy/relay.sh          (BUILD_ONLY=1: build the image locally and stop)
set -euo pipefail
export CLOUDSDK_ACTIVE_CONFIG_NAME=${GCLOUD_CONFIG:-com-tower}
P=${PROJECT:-comtower} R=${REGION:-us-central1} SVC=battle-lab BUCKET=${BUCKET:-comtower-battle-lab}
HERE=${0:A:h:h} LIBAW=${LIBAW:-$HOME/repos/libaw}
$HERE/tools/vendor_libaw.sh
WATER=$HERE/web/awbw-water.json   # AWBW sprite-sheet coordinates: made locally, never committed
[[ -f $WATER ]] || python3 -I $HERE/tools/awbw_water.py
CTX=$(mktemp -d)
mkdir -p $CTX/libaw $CTX/app
cp $LIBAW/Cargo.toml $LIBAW/Cargo.lock $CTX/libaw/
rsync -a --exclude target $LIBAW/crates $LIBAW/data $CTX/libaw/
rsync -a --exclude __pycache__ $HERE/server $HERE/web $HERE/vendor $HERE/requirements.txt $CTX/app/
cp ${0:A:h}/Dockerfile $CTX/
if [[ -n ${BUILD_ONLY:-} ]]; then docker build -t $SVC $CTX; rm -rf $CTX; exit; fi
SA=battle-lab@$P.iam.gserviceaccount.com
gcloud iam service-accounts describe $SA --project $P >/dev/null 2>&1 ||
  gcloud iam service-accounts create battle-lab --project $P --display-name "battle-lab relay" >/dev/null
gcloud storage buckets describe gs://$BUCKET --project $P >/dev/null 2>&1 ||
  gcloud storage buckets create gs://$BUCKET --project $P --location $R --uniform-bucket-level-access >/dev/null
gcloud storage buckets add-iam-policy-binding gs://$BUCKET --member serviceAccount:$SA --role roles/storage.objectAdmin >/dev/null
gcloud run deploy $SVC --project $P --region $R --source $CTX --service-account $SA \
  --allow-unauthenticated --min-instances 1 --max-instances 1 --no-cpu-throttling \
  --cpu 1 --memory 512Mi --concurrency 250 --timeout 3600 \
  --set-env-vars "STORE=gs://$BUCKET/battle-lab,SEASON=season-1,SEASON_START=14508" --quiet
rm -rf $CTX
gcloud run services describe $SVC --project $P --region $R --format 'value(status.url)'

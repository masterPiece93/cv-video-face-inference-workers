# Onboarding Verification Worker

<details>
<summary>Infra / Deployement Guide</summary>

- the **docker file** is at this location : `/worker/onboarding_verification_worker/docker/Dockerfile`
- the **common codebase** is at the root `/common` , `/common_requirements` is required for building the Dockerfile .
- the **core codebase** is at location `/worker/onboarding_verification_worker`
- a **.env** file needs to be mounted at : `/app/workers/onboarding_verification_worker/envs/.env`
- if **service account** needs to be used ( instead of ADC ) in GKE , then the mount path for the gcp.json is : `/app/workers/onboarding_verification_worker/data/gcp.json`
    - IF service account to be used :- the entry in .env  file will be : `GCP__SA_PATH=../data/gcp.json` 
    - ELSE it'll be blank : `GCP__SA_PATH=`
</details>
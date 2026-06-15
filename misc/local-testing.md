# Local testing

## face encoding worker

### Live GCP + Fake Emulator + Personalised Env

**fake pubsub emulator**
- start fake emulator
    ```shell
    docker compose up -d pubsub-emulator
    ```
    - _**fake emulator**_ runs using the project id : `gta-eci-local`
    - _**fake emulator**_ runs on the port : `8085`
    > you need to mention/use this as gcp project & emulator port respectively in .env file that you prepare
- prepare the pubsub emulator
    - run `local_testing/setup_emulator.py`
        > NOTE : it has default values of TOPICS & SUBSCRIPTIONS that it'll create , but if you have a `.env` file ( according to which you want fake pubsub to work ) , you can pass it to this script
 
**gcp bucket**
- gcp bucket will be autmatically used
    - via ADC ( RUN `gcloud auth application-default login`)
    - via SA ( keep the path of you sa file (.json) in *GCP__SA_PATH=* variable)
- make sure that your gcp bucket has the required folder - [refer](#required-data-per-worker)
- if the required data is not present , please generate and feed it in the bucket first
    - use `prepare_processing_data.py` to generate required seed data
    - run `seed_gcs.py` file to upload required seed data to gcs bucket of your choice.
        > NOTE : please read it's docstring accordingly to undersant it working

- start worker for your choice in a seperate terminal
    ```shell
    ENV_NAME=<dev/local/prod> python3 main.py
    ```

- start  a request message publisher script in a seperate terminal
    ```shell
    python3 publish_test_message.py
    ```

- start  a response message listner script in a seperate terminal
    ```shell
    python3 listen_output.py
    ```

**teardown**

```shell
docker compose down
```

```shell
docker compose stop pubsub-emulator
```

---

## face verification worker

> same a [face encoding worker steps](#face-encoding-worker)

The only points to note is :

- use this -> _**gta-ai-eci-workers/workers/`face_verification_worker`/local_testing/**_ directory for the local testing scripts .
- use this -> _**gta-ai-eci-workers/workers/`face_encoding_worker`/local_testing/**_ directory only for emulator configuration .
    - since the emulator configuration for starting the emulator is residing in this directory only.

---

## onboarding verification worker

> same a [face encoding worker steps](#face-encoding-worker)

The only points to note is :

- use this -> _**gta-ai-eci-workers/workers/`onboarding_verification_worker`/local_testing/**_ directory for the local testing scripts .
- use this -> _**gta-ai-eci-workers/workers/`face_encoding_worker`/local_testing/**_ directory only for emulator configuration .
    - since the emulator configuration for starting the emulator is residing in this directory only.

---

<br>

**NOTE : The `test_data*.json` files ( _in each worker's local_testing/ folder_ ) will give you hint of the structure of json message being published to that particulatr worker .**

##### Required Data Per Worker
    - for face encoding worker
    - for face verification worker
    - for onboarding verification worker


## UPDATES NEEDED

- `local_testing/seed_gcs.py`
    - update the module docstrings, it's old , and new cli args were added afterwards .
    - add **help** command to show details
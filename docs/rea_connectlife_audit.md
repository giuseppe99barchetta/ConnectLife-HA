# REA / JADX audit of the ConnectLife Android app

Date: 2026-10-12

This note records only evidence reproduced with the real `jadx-headless-mcp-0.7.1-all.jar` requested for this investigation. It deliberately separates APK-derived facts from values that already exist in the Home Assistant integration.

## APK identity and provenance

The official Google Play listing identifies ConnectLife as package `com.hisense.juconnect.connectlife`:

- Google Play: `https://play.google.com/store/apps/details?id=com.hisense.juconnect.connectlife`
- APKPure page used for the Windows-side copy: `https://apkpure.net/connectlife/com.hisense.juconnect.connectlife/download`

The XAPK downloaded to `%TEMP%\ConnectLife_2.17.1_APKPure.xapk` was `263,273,629` bytes. Its embedded `manifest.json` reports:

- package: `com.hisense.juconnect.connectlife`
- app name: `ConnectLife`
- version name: `2.17.1`
- version code: `2026092401`
- min SDK: `26`
- target SDK: `35`
- split APKs: base + `config.arm64_v8a` + `config.en` + `config.mdpi`

The extracted base APK was `com.hisense.juconnect.connectlife.apk`, size `179,873,027` bytes, SHA-256:

`A16A55D919D8EB8FAB14CC2DBD7F252FE0B49C98C6E8AAE69AEB4E884513CAAB`

The user-uploaded XAPK was confirmed to the prime agent at `/mnt/data/ConnectLife_2.17.1_APKPure.xapk`, with the same filename/version and a ~179.9 MB base APK. This Windows worker could not access `/mnt/data`, so REA was run against the public APKPure copy. I did not compute a cross-environment hash of the uploaded XAPK; byte-for-byte identity with the upload is therefore not claimed.

## Real REA/JADX execution

The JAR exists at:

`C:\Users\peppe\AppData\Local\REA\tools\jadx-headless-mcp-0.7.1-all.jar`

Its `--help` output identifies it as `jadx-headless-mcp` with MCP tools such as `get_app_info`, `get_android_manifest`, `get_class_source`, `search_classes_by_keyword`, xref tools and resource inspection.

The actual base APK was loaded with:

```text
java -jar C:\Users\peppe\AppData\Local\REA\tools\jadx-headless-mcp-0.7.1-all.jar \
  --apk %TEMP%\ConnectLife_2.17.1_APKPure\com.hisense.juconnect.connectlife.apk \
  --resources lite --threads 4
```

Observed loader evidence:

- JADX raw load: `62,938` classes, `442,465` methods, `9,530,106` instructions.
- MCP session exposed `27,531` classes after indexing/filtering.
- successful load time was about 7.9-8.2 seconds in the two reproduced runs.
- MCP `status` returned state `LOADED` and the exact base-APK path above.
- MCP `get_app_info` independently returned package `com.hisense.juconnect.connectlife`, version `2.17.1`, version code `2026092401`, min SDK `26`, target SDK `35`.

`get_main_application_classes_names` found only five classes directly under the manifest package:

1. `com.hisense.juconnect.connectlife.CLApplication`
2. `com.hisense.juconnect.connectlife.CLFileProvider`
3. `com.hisense.juconnect.connectlife.MainActivity`
4. `com.hisense.juconnect.connectlife.R`
5. `com.hisense.juconnect.connectlife.gigya.providers.GoogleProviderWrapper`

This proves the correct app was loaded, while also showing that searching only the manifest package would miss most of the APK's implementation.

With `--resources lite`, `get_strings(filter="hijuconn")` and `get_strings(filter="clife")` both returned zero resource-string hits. This is only a result for the exposed string resources in lite mode; it is not evidence that those strings are absent from code, assets, native libraries or hidden resources.

## Code-search limitation encountered

I attempted real `search_classes_by_keyword(..., search_in="code")` calls for the integration's strongest fingerprints, including `hijuconn`, `f_temp_in`, `f_humidity` and `get_msg_and_channels`.

The unscoped scan forced JADX to decompile a very large set of third-party classes. It produced thousands of decompiler warnings/errors before a reliable result set was returned. Representative failures included:

- `JadxRuntimeException` in several Google/measurement and coroutine classes;
- `JadxOverflowException ... Regions stack size limit reached`;
- `Type inference error: updates count limit reached`;
- repeated `Code restructure failed` warnings.

The scan was interrupted rather than treating incomplete output as evidence. Therefore this audit does **not** claim APK-side confirmation or rejection of the integration's REST endpoints, WebSocket protocol or device property keys yet.

## Home Assistant integration baseline to verify against the APK

The following values are already implemented in the repository and are recorded here only as the comparison baseline. They were not promoted to APK-derived facts by this run.

### REST / push bootstrap currently implemented

`custom_components/hisense_ac_plugin/const.py` defines:

- production API base: `https://juapi-3rd.hijuconn.com`
- OAuth authorize: `https://oauth.hijuconn.com/login`
- OAuth token: `https://oauth.hijuconn.com/oauth/token`
- `/clife-svc/pu/get_device_status_list`
- `/clife-svc/get_property_list`
- `/clife-svc/pu/query_static_data`
- `/device/pu/property/set`
- `/basic/self_check/info`
- `/clife-svc/pu/get_hour_power`

`custom_components/hisense_ac_plugin/websocket.py` currently bootstraps notifications using:

- `POST /msg/registerPhoneDevice` with `phoneCode`
- `GET /msg/get_msg_and_channels` with `pageNo`, `pageSize`, `phoneCode`, `queryType=2`
- dynamic socket URL `wss://{pushServerIp}:{pushServerSslPort}/ws/{pushChannel}?phoneCode={phoneCode}&token={access_token}`
- incoming text frames decoded Base64 -> UTF-8 -> JSON before the integration callback

`models.py` expects bootstrap fields including `pushChannels[].pushChannel`, `pushServerIp`, `pushServerPort`, `pushServerSslPort`, `hbInterval`, `hbFailTimes`, `hasMsgUnread`, and `unreadMsgNum`.

### Device/status keys currently implemented

The integration currently models, among others:

- power: `t_power`
- work mode: `t_work_mode`
- fan speed: `t_fan_speed`
- indoor temperature: `f_temp_in`
- temperature unit: `t_temp_type`
- indoor humidity: `f_humidity`
- target temperature: `t_temp`
- target humidity: `t_humidity`
- energy / consumption: `f_electricity`, `f_power_consumption`

It also defines WebSocket message type names `status_devicestatus` and `device_to_app_notify`, and normalizes WebSocket `onlinestats=1` to the repository's online-state representation.

These are high-priority fingerprints for the next APK pass because a single exact hit can be followed with `get_class_source`, method-level decompilation and xrefs instead of another whole-APK code scan.

## Practical next pass

The next REA run should avoid unscoped `search_in=code` over all 27k classes. The efficient path is:

1. expose full resources/assets and inventory app-owned namespaces and packaged bundles;
2. use metadata-only class/method searches to identify likely ConnectLife networking/device modules;
3. rerun exact endpoint/property searches with a package prefix or small candidate class set;
4. for each exact hit, capture the containing class/method plus xrefs and compare request/response field names against `api.py`, `websocket.py`, parsers and entity platforms;
5. specifically verify whether temperature/humidity fields are updated while powered off and in DRY, rather than inferring that behavior from the current HA implementation.

No Home Assistant production state, deployment, secrets or integration source files were changed during this audit.

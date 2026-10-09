# Phone setup

The phone page needs a secure context: `getUserMedia` only works over **https**
(or on `localhost`). A LAN address over plain http will never show the camera
prompt, so each phone must trust the development root CA created by mkcert.
This is a one-time step per phone.

## 1. Get the root CA file onto the phone

On the Mac, `make certs` prints the path of `rootCA.pem` (it is the file in the
directory shown by `mkcert -CAROOT`). Transfer it with any of:

- AirDrop (iPhone), or email it to yourself, or
- serve it briefly from the Mac on the same Wi-Fi:
  `cd "$(mkcert -CAROOT)" && python3 -m http.server 8088`
  then open `http://<mac-lan-ip>:8088/rootCA.pem` on the phone.
  Stop the server afterwards (Ctrl-C). The root CA is a public certificate; the
  private key `rootCA-key.pem` must never leave the Mac.

## 2. iOS (Safari)

1. Open the file. Safari asks to download a configuration profile: tap **Allow**.
2. **Settings > General > VPN & Device Management**, open the "mkcert ..." profile, tap **Install**.
3. **Settings > General > About > Certificate Trust Settings** and enable full trust for the mkcert root. Without this step Safari still warns.

Minimums: iOS 16.4 or newer (Screen Wake Lock is partial from 16.4 and complete from 18.4; older versions work but the screen may dim).

## 3. Android (Chrome)

1. **Settings > Security (or Security & privacy) > More security settings > Encryption & credentials > Install a certificate > CA certificate**.
2. Choose `rootCA.pem` and confirm the warning.
3. Chrome trusts user-installed CAs. Firefox does not by default; use Chrome.

## 4. Check

Open the URL shown by `make dev` (`https://<mac-lan-ip>:5173/`) or `make run`
(`https://<mac-lan-ip>:8443/`). The page must load without a certificate warning.
On the phone page, the pre-check shows "Secure context: yes". If the check fails
it shows the exact URL scheme to use.

The certificate is issued for the Mac's current LAN IPs; after the Mac changes
network, re-run `.venv/bin/publicsafe certs --force` and restart.

## 5. Manual checklist (iOS Safari and Android Chrome)

Filled in at M3 and later. Record phone model, OS version, browser version and
the outcome of: certificate trusted, pairing, "Streaming" reached, reconnect
after Wi-Fi loss, revoke message, wake lock.

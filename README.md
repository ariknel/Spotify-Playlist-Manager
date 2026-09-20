# Spotify Playlist Manager

Delete or unfollow **lots of Spotify playlists at once**, from a simple desktop app.

Instead of removing playlists one by one in Spotify, you tick the ones you want gone and press one button.

**What you get:**

- Your whole playlist library in one scrollable list (with a checkbox on each)
- Each playlist is labelled **Owned** (you made it) or **Following** (someone else's)
- **Select All** / **Deselect All** buttons
- A confirmation pop-up before anything is deleted
- A progress bar and a Cancel button while it works
- You only log in once - the app remembers you

> **Heads up:** deleting is bulk and fast. Double-check your selection before you confirm.

---

## What does "delete" do?

| Playlist type | What happens |
|---|---|
| **Owned** (you created it) | It is removed from your library. Spotify keeps recently deleted playlists for a while, so you can restore them from **Recover playlists** on your [Spotify account page](https://www.spotify.com/account/). |
| **Following** (someone else's) | You just unfollow it. Nothing changes for the person who made it. |

---

## Setup (about 10 minutes, one time only)

### Step 1 - Install Python

Download Python from **https://www.python.org/downloads/** and install it.

> **Windows:** on the first installer screen, tick **"Add python.exe to PATH"** before clicking Install.

### Step 2 - Download this project

Click the green **Code** button at the top of this GitHub page, then **Download ZIP**. Unzip it somewhere (for example your Desktop).

*(If you know git: `git clone` this repository instead.)*

### Step 3 - Open a terminal in the project folder

- **Windows:** open the unzipped folder in File Explorer, click the address bar at the top, type `cmd` and press **Enter**.
- **Mac:** right-click the folder and choose **New Terminal at Folder**.

A black/white text window opens. All the commands below are typed there.

### Step 4 - Install the requirements

**Windows:**
```
py -m pip install -r requirements.txt
```

**Mac / Linux:**
```
python3 -m pip install -r requirements.txt
```

> Linux only: if the app later complains about `tkinter`, run `sudo apt install python3-tk`.

### Step 5 - Create your own Spotify app (free)

Spotify needs an "app" to give this program permission to manage your playlists. You make it yourself, and it's just for you.

1. Go to **https://developer.spotify.com/dashboard** and log in with your Spotify account.
2. Click **Create app**.
3. Fill in:
   - **App name:** anything, e.g. `My Playlist Manager`
   - **App description:** anything, e.g. `Bulk delete playlists`
   - **Redirect URI:** type exactly `http://127.0.0.1:8888/callback` and click **Add**
   - **Which API/SDKs are you planning to use?** tick **Web API**
4. Tick the terms box and click **Save**.
5. Open your new app and click **Settings**. You'll see your **Client ID**. Click **View client secret** to see the **Client secret**. Keep this page open for the next step.

> **Important:** Spotify currently requires the account that owns the app to have **Spotify Premium**. Without it, deleting will fail with a "403" error.

### Step 6 - Put your Client ID and Secret in the app

In the terminal you opened in Step 3:

**Windows:**
```
copy .env.example .env
notepad .env
```

**Mac / Linux:**
```
cp .env.example .env
nano .env
```

Replace the two placeholder values with the ones from Spotify (no quotes, no spaces):

```
CLIENT_ID=paste_your_client_id_here
CLIENT_SECRET=paste_your_client_secret_here
```

Save the file and close it (Notepad: **File > Save**. Nano: **Ctrl+O**, **Enter**, **Ctrl+X**).

---

## Using the app

Start it (from the same terminal / folder):

**Windows:**
```
py main.py
```

**Mac / Linux:**
```
python3 main.py
```

1. **First time only:** your web browser opens and asks you to approve access. Click **Agree**, then go back to the app. Next time it logs in automatically.
2. Your playlists load in the list.
3. Tick the playlists you want to remove - or use **Select All** / **Deselect All**.
4. Click **Delete Selected**. A pop-up tells you how many playlists will be removed. Confirm to continue.
5. Watch the status bar at the bottom (`Deleting 12/47...`). You can press **Cancel** to stop.
6. When it finishes, the list refreshes by itself.

Other buttons: **Refresh** reloads your playlists, and **Log out** forgets your login (handy for switching to another Spotify account).

**Next time you want to use it**, you only need to do Step 3 (open a terminal in the folder) and run `py main.py` / `python3 main.py`.

---

## Something not working?

| Problem | Fix |
|---|---|
| `'py' is not recognized` or `'python' is not recognized` | Python isn't installed or wasn't added to PATH. Re-run the Python installer and tick **Add python.exe to PATH**. |
| Browser says **INVALID_CLIENT: Invalid redirect URI** | In your Spotify app settings, the Redirect URI must be exactly `http://127.0.0.1:8888/callback`. Add it and click **Save**. |
| **Invalid client** error | The Client ID or Client Secret in your `.env` file is wrong. Copy them again from the Spotify dashboard. |
| App says **CLIENT_ID and/or CLIENT_SECRET are missing** | You haven't created the `.env` file yet, or it still has the placeholder text. Redo Step 6. |
| **403** error when deleting | The Spotify account that owns the app needs **Premium**. If you're logging in with a *different* account than the owner, add it in the dashboard under your app > **Settings > User Management**. |
| Login window/port error mentioning **8888** | Another program is using port 8888. Close it and try again. |
| Wrong account, or login seems stuck | Click **Log out** in the app, then **Log in** again. |
| "Rate limited" message | Spotify asked the app to slow down. It waits and retries by itself. If it gives up, wait a few minutes and run it again - playlists already deleted stay deleted. |

---

## Good to know

- Playlists you **follow** show **"-"** instead of a track count. That's a Spotify limitation - it only shares track counts for playlists you own.
- **Never share or upload your `.env` file** (it holds your Client Secret) or the `.spotify_token_cache` file (it holds your login). Both are already listed in `.gitignore`, so git won't upload them.
- This project is not affiliated with or endorsed by Spotify.

---

## For developers

| File | Purpose |
|---|---|
| `main.py` | Entry point |
| `auth.py` | Spotify OAuth login, `.env` credentials, token cache |
| `spotify_client.py` | Fetch playlists (paginated), delete/unfollow, retries and rate-limit handling |
| `ui.py` | customtkinter interface |
| `.env.example` | Template for your credentials |

Built with [spotipy](https://spotipy.readthedocs.io/) and [customtkinter](https://github.com/TomSchimansky/CustomTkinter). Unfollowing uses Spotify's `DELETE /me/library` endpoint (the replacement for the older `DELETE /playlists/{id}/followers`).

The app requests the scopes `playlist-read-private`, `playlist-modify-private`, `playlist-modify-public`, `user-library-modify` and `user-follow-modify`. They are listed in `SCOPES` in `auth.py`.

Collaborative playlists owned by someone else may not appear in the list, because the `playlist-read-collaborative` scope isn't requested. Add it to `SCOPES` in `auth.py` if you want them shown.

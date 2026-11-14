"""Tests for the PCStation game launcher back end. Run: python -m unittest discover -s pc/tests -v"""
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pcstation_launcher as L  # noqa: E402


def write(path, text="", mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode) as f:
        f.write(text)


def acf(appid, name, flags=4):
    return f'"AppState"\n{{\n\t"appid"\t\t"{appid}"\n\t"name"\t\t"{name}"\n\t"StateFlags"\t\t"{flags}"\n}}\n'


class SteamTests(unittest.TestCase):
    def test_vdf_parser_handles_escapes_and_nesting(self):
        d = L.parse_vdf('"LibraryFolders" { "0" { "path" "C:\\\\Games\\\\Steam" "apps" { "10" "1234" } } "1" "D:\\\\Old" }')
        lf = d["libraryfolders"]
        self.assertEqual(lf["0"]["path"], "C:\\Games\\Steam")
        self.assertEqual(lf["0"]["apps"]["10"], "1234")
        self.assertEqual(lf["1"], "D:\\Old")

    def test_scan_steam_finds_installed_games_across_libraries(self):
        with tempfile.TemporaryDirectory() as t:
            root, lib2 = os.path.join(t, "Steam"), os.path.join(t, "SteamLibrary")
            apps, apps2 = os.path.join(root, "steamapps"), os.path.join(lib2, "steamapps")
            os.makedirs(apps2)
            write(os.path.join(apps, "libraryfolders.vdf"),
                  '"libraryfolders" { "0" { "path" "%s" } "1" { "path" "%s" } }' % (root.replace("\\", "\\\\"), lib2.replace("\\", "\\\\")))
            write(os.path.join(apps, "appmanifest_100.acf"), acf(100, "Portal Clone"))
            write(os.path.join(apps, "appmanifest_101.acf"), acf(101, "Half Installed", flags=1026))   # still downloading
            write(os.path.join(apps, "appmanifest_228980.acf"), acf(228980, "Steamworks Common Redistributables"))
            write(os.path.join(apps, "appmanifest_102.acf"), acf(102, "Proton 9.0"))
            write(os.path.join(apps2, "appmanifest_300.acf"), acf(300, "Racing Game"))
            write(os.path.join(root, "appcache", "librarycache", "100_library_600x900.jpg"), "jpg")
            write(os.path.join(root, "appcache", "librarycache", "300", "abc123", "library_600x900.jpg"), "jpg")
            found = {e["id"]: e for e in L.scan_steam(root)}
            self.assertEqual(sorted(found), ["steam:100", "steam:300"])
            self.assertEqual(found["steam:100"]["launch"]["value"], "steam://rungameid/100")
            self.assertTrue(found["steam:100"]["art"].endswith("100_library_600x900.jpg"))
            self.assertTrue(found["steam:300"]["art"].endswith("library_600x900.jpg"))      # newer cache layout


class EpicTests(unittest.TestCase):
    def test_scan_epic_skips_dlc_and_incomplete(self):
        with tempfile.TemporaryDirectory() as t:
            def item(name, **kw):
                write(os.path.join(t, name + ".item"), json.dumps(dict(kw)))
            item("a", AppName="Fortnite", DisplayName="Fortnite", MainGameAppName="Fortnite")
            item("b", AppName="FortniteDLC", DisplayName="Some DLC", MainGameAppName="Fortnite")
            item("c", AppName="Broken", DisplayName="Broken Game", bIsIncompleteInstall=True)
            write(os.path.join(t, "readme.txt"), "ignore me")
            found = L.scan_epic(t)
            self.assertEqual([e["id"] for e in found], ["epic:Fortnite"])
            self.assertIn("com.epicgames.launcher://apps/Fortnite", found[0]["launch"]["value"])


class LibraryTests(unittest.TestCase):
    def test_library_persists_between_runs(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "library.json")
            lib = L.Library(p)
            e1 = L.make_entry("steam", "1", "One", {"type": "uri", "value": "steam://rungameid/1"})
            e2 = L.make_entry("epic", "Two", "Two", {"type": "uri", "value": "x"})
            self.assertEqual(lib.add([e1, e2, e1]), 2)            # duplicates ignored
            lib.rename("steam:1", "  Game   One  ")
            lib.remove("epic:Two")
            again = L.Library(p)                                    # "restart"
            self.assertEqual([g["name"] for g in again.games], ["Game One"])

    def test_pretty_name(self):
        self.assertEqual(L.pretty_name("C:/x/my_cool-game.exe"), "My Cool-Game")
        self.assertEqual(L.pretty_name("Chrome.lnk"), "Chrome")


class BrowserTests(unittest.TestCase):
    def test_list_dir_shows_dirs_and_programs_only(self):
        with tempfile.TemporaryDirectory() as t:
            write(os.path.join(t, "Game", "game.exe"))
            write(os.path.join(t, "notes.txt"))
            write(os.path.join(t, "run.bat"))
            write(os.path.join(t, ".hidden", "x.exe"))
            r = L.list_dir(t)
            self.assertEqual([(e["name"], e["type"]) for e in r["entries"]], [("Game", "dir"), ("run.bat", "file")])
            self.assertEqual(L.list_dir(os.path.join(t, "Game"))["parent"], os.path.abspath(t))
            self.assertIsNone(L.list_dir("")["parent"])


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.opened = []
        steam = L.make_entry("steam", "7", "Seven", {"type": "uri", "value": "steam://rungameid/7"})
        self.srv = L.Launcher(get_players=lambda: [{"slot": 1, "name": "Pixel", "connected": True}], directory=self.tmp.name,
                              opener=lambda *a: self.opened.append(a), scanners=[lambda: [steam]]).start()

    def tearDown(self):
        self.srv.stop()
        self.tmp.cleanup()

    def call(self, path, body=None, token=True, host=None):
        h = {"X-Token": self.srv.token} if token else {}
        if host:
            h["Host"] = host
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        req = urllib.request.Request(f"http://127.0.0.1:{self.srv.port}{path}", data=data, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_security(self):
        self.assertEqual(self.call("/api/library", token=False)[0], 403)
        self.assertEqual(self.call("/api/launch", {"id": "x"}, token=False)[0], 403)
        self.assertEqual(self.call("/api/library", host="evil.example.com")[0], 403)           # DNS-rebinding style
        with urllib.request.urlopen(f"http://127.0.0.1:{self.srv.port}/", timeout=5) as r:      # the page itself needs no token
            self.assertIn(b"PCStation", r.read())

    def test_scan_select_save_launch_flow(self):
        scan = self.call("/api/scan")[1]["games"]
        self.assertEqual([g["id"] for g in scan], ["steam:7"])
        self.assertEqual(self.call("/api/add", {"ids": ["steam:7", "steam:bogus"]})[1]["added"], 1)
        self.assertEqual(self.call("/api/scan")[1]["games"], [])                                # already in library
        self.assertEqual(self.call("/api/launch", {"id": "steam:7"})[0], 200)
        self.assertEqual(self.opened, [("steam://rungameid/7",)])
        self.assertEqual(self.call("/api/launch", {"id": "steam:not-in-library"})[0], 404)
        self.assertEqual(self.call("/api/players")[1]["players"][0]["name"], "Pixel")
        self.assertEqual(L.Library(os.path.join(self.tmp.name, "library.json")).public()[0]["name"], "Seven")   # saved to disk

    def test_custom_app_add_and_launch(self):
        exe = os.path.join(self.tmp.name, "Cool_Game", "cool_game.exe")
        write(exe, "MZ")
        r = self.call("/api/add", {"path": exe})[1]
        self.assertEqual(r["games"][0]["name"], "Cool Game")
        self.call("/api/launch", {"id": r["games"][0]["id"]})
        self.assertEqual(self.opened[0][0], exe)
        self.assertEqual(self.opened[0][1], os.path.dirname(exe))                               # starts in the game's folder
        notexe = os.path.join(self.tmp.name, "a.txt")
        write(notexe)
        self.assertEqual(self.call("/api/add", {"path": notexe})[0], 400)

    def test_rename_remove_and_art(self):
        art = os.path.join(self.tmp.name, "c.jpg")
        write(art, "JPEGDATA")
        self.srv.library.add([dict(L.make_entry("steam", "9", "Nine", {"type": "uri", "value": "u"}, art))])
        self.call("/api/rename", {"id": "steam:9", "name": "Number Nine"})
        self.assertEqual(self.call("/api/library")[1]["games"][0]["name"], "Number Nine")
        req = urllib.request.Request(f"http://127.0.0.1:{self.srv.port}/art/steam%3A9?t={self.srv.token}")
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.read(), b"JPEGDATA")
        self.assertEqual(self.call("/api/remove", {"id": "steam:9"})[1]["games"], [])


class ProfileTests(unittest.TestCase):
    def test_users_and_controller_assignments_persist(self):
        with tempfile.TemporaryDirectory() as t:
            path = os.path.join(t, "profile.json")
            pr = L.Profile(path)
            self.assertEqual([u["name"] for u in pr.users], ["Player 1", "Player 2", "Player 3", "Player 4"])
            new = pr.add_user("  Max   Power ")
            pr.rename_user("u1", "Dad")
            pr.set_controller("phone:abc123", user=new["id"], alias="Max's phone")
            pr.set_controller("pad:054c:0ce6:0", user="u2")
            again = L.Profile(path)                                     # "restart"
            self.assertEqual(again.user(new["id"])["name"], "Max Power")
            self.assertEqual(again.user("u1")["name"], "Dad")
            self.assertEqual(again.controllers["phone:abc123"], {"user": new["id"], "alias": "Max's phone"})
            again.remove_user(new["id"])                                # their controller becomes unassigned, not lost
            self.assertEqual(again.controllers["phone:abc123"]["user"], "")
            again.set_controller("pad:054c:0ce6:0", forget=True)
            self.assertNotIn("pad:054c:0ce6:0", again.controllers)

    def test_cannot_delete_last_user(self):
        with tempfile.TemporaryDirectory() as t:
            pr = L.Profile(os.path.join(t, "p.json"))
            for u in list(pr.users)[:-1]:
                pr.remove_user(u["id"])
            self.assertFalse(pr.remove_user(pr.users[0]["id"]))
            self.assertEqual(len(pr.users), 1)


class ArtTests(unittest.TestCase):
    def test_steam_art_finds_cover_hero_logo_in_both_layouts(self):
        with tempfile.TemporaryDirectory() as t:
            c = os.path.join(t, "appcache", "librarycache")
            write(os.path.join(c, "5_library_600x900.jpg"), "x"); write(os.path.join(c, "5_library_hero.jpg"), "x"); write(os.path.join(c, "5_logo.png"), "x")
            a = L.find_steam_art(t, "5")
            self.assertTrue(a["cover"].endswith("5_library_600x900.jpg") and a["hero"].endswith("5_library_hero.jpg") and a["logo"].endswith("5_logo.png"))
            write(os.path.join(c, "6", "h", "header.jpg"), "x"); write(os.path.join(c, "6", "h", "logo.png"), "x")
            b = L.find_steam_art(t, "6")
            self.assertTrue(b["cover"].endswith("header.jpg") and b["hero"].endswith("header.jpg"))   # header is the fallback
            self.assertTrue(b["logo"].endswith("logo.png"))

    def test_epic_catalog_pictures(self):
        with tempfile.TemporaryDirectory() as t:
            import base64
            items = [{"id": "cat1", "keyImages": [{"type": "DieselGameBoxTall", "url": "https://cdn.example/tall.jpg"},
                                                  {"type": "DieselGameBox", "url": "https://cdn.example/wide.jpg"},
                                                  {"type": "Thumbnail", "url": "http://insecure/x.jpg"}]}]
            write(os.path.join(t, "catcache.bin"), base64.b64encode(json.dumps(items).encode()).decode())
            write(os.path.join(t, "m", "a.item"), json.dumps({"AppName": "Game", "DisplayName": "Game", "CatalogItemId": "cat1"}))
            e = L.scan_epic(os.path.join(t, "m"), os.path.join(t, "catcache.bin"))[0]
            self.assertEqual(e["art_urls"]["cover"], "https://cdn.example/tall.jpg")
            self.assertEqual(e["art_urls"]["hero"], "https://cdn.example/wide.jpg")


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.buzzed, self.downloads = [], []

        def fake_download(url, dest):
            self.downloads.append(url)
            write(dest, "IMG")
            return True
        steam = L.make_entry("steam", "42", "No Art Yet", {"type": "uri", "value": "steam://rungameid/42"})
        self.srv = L.Launcher(directory=self.tmp.name, opener=lambda *a: None, scanners=[lambda: [steam]],
                              identify=lambda pid: self.buzzed.append(pid) or True, download=fake_download).start()

    def tearDown(self):
        self.srv.stop()
        self.tmp.cleanup()

    def call(self, path, body=None):
        h = {"X-Token": self.srv.token}
        data = None
        if body is not None:
            data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
        req = urllib.request.Request(f"http://127.0.0.1:{self.srv.port}{path}", data=data, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_missing_steam_art_downloads_in_background(self):
        import time
        self.call("/api/scan")
        self.call("/api/add", {"ids": ["steam:42"]})
        for _ in range(50):
            g = self.srv.library.get("steam:42")
            if g["art"] and g["hero"] and g["logo"]:
                break
            time.sleep(0.05)
        self.assertTrue(g["art"] and g["hero"] and g["logo"])
        self.assertIn("https://cdn.cloudflare.steamstatic.com/steam/apps/42/library_hero.jpg", self.downloads)
        self.assertEqual(self.call("/api/library")[1]["games"][0]["hero"], True)

    def test_art_download_can_be_switched_off(self):
        self.call("/api/settings", {"art_download": False})
        self.call("/api/scan")
        self.call("/api/add", {"ids": ["steam:42"]})
        import time
        time.sleep(0.3)
        self.assertEqual(self.downloads, [])

    def test_user_and_controller_endpoints(self):
        prof = self.call("/api/profile")[1]
        self.assertEqual(len(prof["users"]), 4)
        created = self.call("/api/user", {"action": "add", "name": "Guest"})[1]["created"]
        self.call("/api/user", {"action": "rename", "id": created, "name": "Visitor"})
        self.call("/api/controller", {"key": "phone:z9", "user": created, "alias": "Left phone"})
        p = self.call("/api/profile")[1]
        self.assertEqual([u["name"] for u in p["users"]][-1], "Visitor")
        self.assertEqual(p["controllers"]["phone:z9"], {"user": created, "alias": "Left phone"})
        self.assertEqual(L.Profile(os.path.join(self.tmp.name, "profile.json")).controllers["phone:z9"]["user"], created)   # saved

    def test_identify_and_settings(self):
        self.assertTrue(self.call("/api/identify", {"pid": "abc"})[1]["ok"])
        self.assertEqual(self.buzzed, ["abc"])
        self.call("/api/settings", {"sound": False, "bogus": 1})
        s = self.call("/api/profile")[1]["settings"]
        self.assertFalse(s["sound"])
        self.assertNotIn("bogus", s)
        self.assertFalse(L.load_settings(self.tmp.name)["sound"])                       # saved

    def test_custom_picture_for_a_game(self):
        self.srv.library.add([L.make_entry("custom", "1", "My App", {"type": "exe", "value": "/x", "cwd": "/", "args": ""})])
        img = os.path.join(self.tmp.name, "mine.png")
        write(img, "PNGDATA")
        r = self.call("/api/setart", {"id": "custom:1", "path": img})
        self.assertEqual(r[0], 200)
        self.assertTrue(r[1]["games"][0]["art"] and r[1]["games"][0]["hero"])
        req = urllib.request.Request(f"http://127.0.0.1:{self.srv.port}/art/custom%3A1?kind=hero&t={self.srv.token}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.read(), b"PNGDATA")
        txt = os.path.join(self.tmp.name, "n.txt")
        write(txt)
        self.assertEqual(self.call("/api/setart", {"id": "custom:1", "path": txt})[0], 400)

    def test_browser_image_mode(self):
        write(os.path.join(self.tmp.name, "pics", "a.jpg"))
        write(os.path.join(self.tmp.name, "pics", "b.exe"))
        names = [e["name"] for e in L.list_dir(os.path.join(self.tmp.name, "pics"), "image")["entries"]]
        self.assertEqual(names, ["a.jpg"])


if __name__ == "__main__":
    unittest.main()

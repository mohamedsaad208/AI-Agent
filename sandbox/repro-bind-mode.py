"""Scratch reproduction for the review: can a chat dropped onto a project inherit Change mode
and that folder's Auto-Apply switch? Delete after the review is written."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from ai_code_engineer.engine import project_key  # noqa: E402
import test_controller as tc  # noqa: E402


class BindInheritsMode(tc.BranchTests):
    def show(self, where, controller):
        print("%-22s kind=%-8s repo=%-10s composer=%-6s auto=%s" % (
            where, controller.branch.get("kind"), Path(controller.repo or "-").name,
            controller.composer, controller.auto_apply))

    def armed_project(self, controller):
        key = project_key(str(self.repo))
        controller.projects[key] = str(self.repo.resolve())
        controller._select_branch("project", key, composer="change")
        controller.join()
        controller.set_composer("change")
        controller.set_auto_apply(True)
        controller.join()
        return key

    def test_drop_a_persisted_chat_onto_a_project_left_in_change_mode(self):
        controller = self.fresh()
        self.ask(controller, "HI")
        key = self.armed_project(controller)
        self.show("project armed", controller)

        controller.new_chat()
        self.ask(controller, "HI")            # a chat is only on disk once it has a turn
        chat_id = controller.chat_id
        self.show("new chat", controller)
        controller.bind_chat(chat_id, key)
        controller.join()
        self.show("after the drop", controller)
        print("   status              :", controller.status[:80])

        before = (self.repo / "calculator.py").read_text(encoding="utf-8")
        self.ask(controller, "What does add do here?")
        after = (self.repo / "calculator.py").read_text(encoding="utf-8")
        print("   action calls        :", len(self.model.actions), " prose calls:", len(self.model.prose))
        print("   dialogs             :", [row.get("title") for row in controller.asked])
        print("   file changed        :", before != after, "|", after.splitlines()[-1].strip())
        print("   session state       :", (controller.session or {}).get("state"))

    def test_reopening_the_bound_chat_after_a_restart(self):
        controller = self.fresh()
        self.ask(controller, "HI")
        key = self.armed_project(controller)
        controller.new_chat()
        self.ask(controller, "HI")
        chat_id = controller.chat_id
        controller.bind_chat(chat_id, key)
        controller.join()
        again = self.fresh()
        again.catalogs["Ollama"] = controller.catalogs["Ollama"]
        again.open_chat(self.app_dir / ".agent-chats" / chat_id / "chat.json")
        self.show("reopened", again)
        print("   pref composer       :", {k[-8:]: v for k, v in again._composer_pref.items()})
        print("   pref auto           :", {k[-8:]: v for k, v in again._auto_pref.items()})
        self.ask(again, "What does add do here?")
        print("   action calls        :", len(self.model.actions), " prose calls:", len(self.model.prose))
        print("   file on disk        :", (self.repo / "calculator.py").read_text(encoding="utf-8").splitlines()[-1].strip())


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "BindInheritsMode"], exit=False)

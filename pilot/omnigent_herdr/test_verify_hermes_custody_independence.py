"""Read-only custody preflight denies false independent-admin claims."""
from __future__ import annotations

from unittest.mock import patch
import unittest

import verify_hermes_custody_independence as witness


def candidate(*, user="scott", uid=1000,
              groups=("scott", "adm", "sudo")):
    return {"observed_node": "synthetic-candidate",
            "login_user": user, "uid": uid, "groups": list(groups)}


def all_candidates(**kw):
    return {x: candidate(**kw) for x in witness.KNOWN_HOSTS}


class HermesCustodyReadOnlyTests(unittest.TestCase):
    def test_observed_same_privileged_login_is_independence_nogo(self):
        out=witness.classify(local_user="scott", local_uid=1000,
                             observations=all_candidates())
        self.assertEqual(out["shared_privileged_principals_observed"],3)
        self.assertEqual(out["ssh_observations_verified"],3)
        self.assertIn("shared_privileged_principal_observed",out["violations"])
        self.assertFalse(out["independent_administrator_verified"])
        self.assertEqual(out["decision"],"HOLD")

    def test_readonly_access_to_physical_node_does_not_make_it_independent(self):
        data=all_candidates(user="custodian",groups=("custodian",))
        result=witness.classify(local_user="scott",local_uid=1000,
                                observations=data)
        self.assertEqual(result["ssh_observations_verified"],3)
        self.assertEqual(result["shared_privileged_principals_observed"],0)
        self.assertFalse(result["independent_administrator_verified"])
        self.assertFalse(result["independently_pinned_signer_custody_verified"])
        self.assertFalse(result["rollback_resistant_external_head_verified"])

    def test_sudo_and_docker_are_both_privileged_warning(self):
        for grp in ("sudo","wheel","docker","lxd","libvirt"):
            with self.subTest(group=grp):
                result=witness.classify(
                    local_user="scott",local_uid=1000,
                    observations=all_candidates(groups=("scott",grp)))
                self.assertEqual(result["shared_privileged_principals_observed"],3)

    def test_different_unprivileged_account_still_not_verified(self):
        result=witness.classify(local_user="scott",local_uid=1000,
                                observations=all_candidates(
                                    user="nonoperator",uid=1400,
                                    groups=("nonoperator",)))
        self.assertFalse(result["independent_administrator_verified"])
        self.assertEqual(result["decision"],"HOLD")

    def test_missing_candidate_fails_closed(self):
        result=witness.classify(local_user="scott",local_uid=1000,
                                observations={"lenovo":candidate()})
        self.assertEqual(result["ssh_observations_verified"],1)
        self.assertIn("candidate_inventory_incomplete",result["violations"])
        self.assertIn("unqualified_remote_principal",result["violations"])

    def test_unknown_fourth_candidate_fails_inventory(self):
        data=all_candidates()
        data["attacker-host"]=candidate()
        out=witness.classify(local_user="scott",local_uid=1000,
                             observations=data)
        self.assertIn("candidate_inventory_incomplete",out["violations"])

    def test_untrusted_uid_and_boolean_spoofing_fails(self):
        for uid in (True,False,-1,"1000",None):
            with self.subTest(uid=uid):
                out=witness.classify(local_user="scott",local_uid=uid,
                                     observations=all_candidates())
                self.assertIn("local_identity_untrusted",out["violations"])

    def test_remote_bad_record_never_qualifies(self):
        for record in (None,True,{},{"independent":True},
                       {"observed_node":"x","login_user":"scott",
                        "uid":True,"groups":["sudo"]}):
            with self.subTest(record=str(record)[:24]):
                data=all_candidates()
                data["lenovo"]=record
                out=witness.classify(local_user="scott",local_uid=1000,
                                     observations=data)
                self.assertEqual(out["ssh_observations_verified"],2)
                self.assertFalse(out["independent_administrator_verified"])

    def test_ssh_response_must_have_exact_bounded_metadata(self):
        correct="NODE=synthetic-machine\nUSER=scott\nUID=1000\nGROUPS=scott adm sudo\n"
        parsed=witness.parse_remote(correct)
        self.assertIsNotNone(parsed)
        self.assertIn("sudo",parsed["groups"])
        for raw in ("", "hello", correct+"EXTRA=1\n",
                    correct.replace("UID=1000","UID=root"),
                    correct.replace("GROUPS=scott adm sudo","GROUPS=scott sudo sudo"),
                    correct.replace("NODE=synthetic-machine","NODE=../escape"),
                    "NODE=x\nUSER=scott\nUID=1000\nGROUPS=scott\n"*500):
            with self.subTest(raw=raw[:35]):
                self.assertIsNone(witness.parse_remote(raw))

    def test_default_never_invokes_ssh(self):
        with patch.object(witness.subprocess,"run") as shell:
            result=witness.classify(local_user=None,local_uid=None,
                                    observations=None)
        shell.assert_not_called()
        self.assertFalse(result["production_deployment_authorized"])
        self.assertFalse(result["independent_administrator_verified"])

    def test_physical_probe_uses_pinned_known_hosts_and_readonly_commands(self):
        from types import SimpleNamespace
        observed=[]
        def fake_run(args, **kwargs):
            observed.append((args,kwargs))
            return SimpleNamespace(
                returncode=0,
                stdout="NODE=synthetic-machine\nUSER=scott\nUID=1000\n"
                       "GROUPS=scott adm sudo\n")
        with patch.object(witness.os,"geteuid",return_value=1000):
            with patch.object(witness.os,"getuid",return_value=1000):
                with patch.object(witness.os,"uname",return_value=SimpleNamespace(nodename="x1-370")):
                    with patch.object(witness.pwd,"getpwuid",
                                      return_value=SimpleNamespace(pw_name="scott")):
                        with patch.object(witness.subprocess,"run",side_effect=fake_run):
                            result=witness.audit_physical_readonly()
        self.assertEqual(result["shared_privileged_principals_observed"],3)
        self.assertEqual(len(observed),3)
        for i,(args,kw) in enumerate(observed):
            self.assertIn(witness.KNOWN_HOSTS[i],args)
            self.assertEqual(args[-1],witness.READ_COMMAND)
            self.assertIn("StrictHostKeyChecking=yes",args)
            self.assertTrue(kw["capture_output"])
            self.assertLessEqual(kw["timeout"],10)

    def test_ssh_failure_does_not_imply_independent_custody(self):
        from types import SimpleNamespace
        def fail_run(*a,**kw):
            return SimpleNamespace(returncode=255,stdout="")
        with patch.object(witness.os,"geteuid",return_value=1000),\
             patch.object(witness.os,"getuid",return_value=1000),\
             patch.object(witness.os,"uname",return_value=SimpleNamespace(nodename="x1-370")),\
             patch.object(witness.pwd,"getpwuid",
                               return_value=SimpleNamespace(pw_name="scott")),\
             patch.object(witness.subprocess,"run",side_effect=fail_run):
            out=witness.audit_physical_readonly()
        self.assertEqual(out["ssh_observations_verified"],0)
        self.assertFalse(out["independent_administrator_verified"])

    def test_report_omits_ssh_key_names_and_sensitive_storage(self):
        out=witness.classify(local_user="scott",local_uid=1000,
                             observations=all_candidates())
        rendered=str(out)
        for forbidden in ("/media/scott","id_ed25519",
                          "FLEET_ROUTER_BEARER_TOKEN","ssh-rsa"):
            self.assertNotIn(forbidden,rendered)


if __name__ == "__main__":
    unittest.main()

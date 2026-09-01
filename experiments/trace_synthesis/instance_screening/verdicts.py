#!/usr/bin/env python3
"""The hand-written verdict table, and the writer for ``candidates.json``.

Every verdict in this file is a human reading of one instance against the
determinacy criterion; the screens in ``screens.py`` only decide which
instances got read hardest. Keeping the two apart is deliberate — a screen
never votes.

Each row is ``(index in issue #261, verdict, category, confidence, evidence)``.
The evidence field is the point: it has to let a reviewer re-derive the verdict
from the parquet and the repository at ``base_commit`` without trusting this
file.

Usage::

  direnv exec . uv run python experiments/trace_synthesis/instance_screening/verdicts.py
"""
import json, pathlib
from swe_lab.datasets.loader import load_dataset

HERE = pathlib.Path(__file__).resolve().parent
V = [
 (0,"bad","misleading_prompt","high","requirements say \"The method\" three times for the same three units the interface types \"Type: Function\"; the two halves of the task statement disagree and the graded tests only accept one placement."),
 (1,"bad","underspecified_prompt","high","test_api_parameters asserts the destinationAddress->dst_address API field mapping; none of the three prompt fields contains those spellings, no sibling bigip_message_routing_* module exists at base_commit, and the one in-repo precedent (bigiq_application_*.py) maps to destination_address instead."),
 (2,"good","","high","sessionKeyProperty is named in the prompt; the one signature change (sessionKeys.delete drops uid) is stated: \"must automatically derive the current user from the context.Context ... without passing an explicit user ID\"."),
 (3,"bad","overly_strict_tests","high","compile gate. base report/util.go:523 is `func diff(curResults, preResults models.ScanResults) (diffed models.ScanResults, err error)`; gold adds two bool parameters AND drops the error return. The graded test assigns one value. No prompt field mentions the return arity, and keeping (result, err) is the conservative choice."),
 (4,"good_with_caveat","","medium","checkEventValidity and the four CalendarEventValidity members are declared in the interface and every graded assertion follows from requirements. Caveats: fail_to_pass is 107 whole test suites with pass_to_pass empty, so one unrelated flake fails the instance; and TranslationKeysTest requires en/de/de_sie key sets to be identical, which punishes wiring the new UserErrors unless all three translation files are edited - never stated. Also note requirements give the precedence as invalid > pre-1970 > ordering while gold implements invalid > ordering > pre-1970; no graded case distinguishes them."),
 (5,"good","","high","interface gives file path, function name, input, output and the rule verbatim; one graded test; 22-line gold patch; all five screens silent. NOTE: image family unrunnable (see runnability)."),
 (6,"bad","overly_strict_tests","high","test asserts ...apiKey equals the lastFMAPIKey symbol; requirements only say \"fall back to a built-in shared API key\" and never name it (steered-impl's dossier)."),
 (7,"bad","overly_strict_tests","high","test calls o.parseRpmQfLine(line) directly; requirements name only pkgPs/postScan/getOwnerPkgs and never ask for the extraction, and the interface field says \"No new interfaces are introduced\" (steered-impl's dossier)."),
 (8,"bad","overly_strict_tests","high","compile gate. base lib/backend/report.go:294 is `func buildKeyLabel(key []byte, sensitivePrefixes []string) string` and already implements the identical masking; base report_test.go:69-83 already holds the identical expectation table. The whole graded delta is the parameter type []byte -> string, which no prompt field mentions. The interface field pins MaskKeyName while TestBuildKeyLabel grades buildKeyLabel."),
 (9,"good","","high","gold only changes the private _parse_value(self, val) -> (self, kind, val); the graded tests call klass().to_py(val) throughout. The observed failure was a genuine regression the agent could have seen by running the existing suite (steered-impl)."),
 (10,"good","","high","requirements are unusually exact - the gRPC code Internal and the message \"failed to fetch list of flags\" verbatim, the BOOLEAN/VARIANT+Enabled rule, the default namespace. The interface declares Storer's method signature and New's full new signature, so the one signature change is stated. The four token alarms (ListWithOptions, NewNamespace, Results, FlagType_VARIANT_FLAG_TYPE) are all pre-existing repo API."),
 (11,"good","","high","arraySmoothingResample and arrayRescale are pure additions; requirements bullets 8-9 spell out the smoothing algorithm (neighbour-pair averaging at alternating interior positions excluding endpoints; repeat until length <= 2*points) and all four graded downsample cases reproduce under that reading. The three arrayFastResample entries in fail_to_pass fail at base only because the test module cannot import the two new names. Token alarms 100/curve/seeing are words in comments."),
 (12,"bad","overly_strict_tests","high","interface field reads \"No new interfaces are introduced\" while fail_to_pass is Test_ensure, which calls a brand-new unexported `ensure(servers, path, scanResults, generateFunc func() (string, error)) (needsOverwrite bool, err error)`. Its name, its parameter order, and the fact that it is separate from EnsureUUIDs are all unstated."),
 (13,"good","","medium","the whole task is a rename requirements state verbatim (\"Rename the confidence label CpeNameMatch to CpeVersionMatch across all definitions, usages, and string representations\") plus a new CpeVendorProductMatch with the score 10 given. The two graded tests are pre-existing tests re-pointed at the new name."),
 (14,"bad","overly_strict_tests","high","TestProcessStateGetState grades a newly added unexported processState.getState() and a componentStateEnum; lib/service/state.go exists at base with processState and stateOK but neither getState nor any per-component map. No prompt field gives those names. The interface field declares only SetOnHeartbeat, which the graded tests never touch - the same shape as teleport-b4e7cd3a."),
 (15,"bad","underspecified_prompt","high","tests require `version: 1.0` as the supported document version and assert EqualError(err, \"unsupported version: 5.0\"); the prompt says only \"validate that the document version is supported\" and never gives a version value or an error format."),
 (16,"good","","medium","requirements give a worked example for every one of the ten graded cases (drive.env.proton.black -> drive.proton.local:8888 etc.) and the interface declares the file and function. NOTE: image family unrunnable."),
 (17,"good","","high","the payload reshape from an array to a single {tid, order} is stated verbatim (\"accepts a single payload containing the topic identifier (tid) and a zero-based target position (order)\"); the three graded behaviours (no-privileges error, no-op on an unpinned topic, relative order preserved) each map to a requirements bullet. All four screens silent. Soft spot, and a useful one: order 0 means the top of the pinned list as displayed (getSortedSetRevRange), the opposite direction from the old score-as-order behaviour - derivable but easy to get backwards."),
 (18,"good","","medium","the exact error text is produced by the test's own fake FileSystem (newErrMissingFile), so the solver only has to propagate it unwrapped, which requirements demand (\"path-qualified error in the exact form\"). Requirements also state explicitly that they do not prescribe internal function names. One graded test, and it is an end-to-end SCP flow."),
 (19,"good","","high","requirements state the optional cancellation parameter and, bullet by bullet, the exact result object the single graded test asserts. The signature alarm is benign: the added parameter is optional and stated. NOTE: image family unrunnable."),
 (20,"good","","medium","the interface field is exhaustive (every exported type, constructor and method of the new tokencount.go) and requirements state the two changed signatures verbatim, including `(any, *model.TokenCount, error)`. The expected token totals 721/729/932 rest on perMessage/perRole/perRequest, which already exist at base_commit - the gold diff removes lines that use them."),
 (21,"bad","overly_strict_tests","low","LruCache.values() ordering is asserted as an exact array in about eight graded tests - set a then b gives [\"a value\", \"b value\"], fixing least-recent-first - while requirements say only \"iterates current contents in the cache's internal order\", which fixes no direction at all. Low confidence, and deliberately so: least-recent-first is the more common convention, so a careful solver may well land on it. Note the snapshot screen does NOT condemn this one - its test patch does carry a committed snapshot, but for Pill-test.tsx, which no fail_to_pass entry names; that snapshot is collateral in the same upstream PR, not the grading mechanism."),
 (22,"good_with_caveat","","medium","requirements state both renames verbatim (\"The function previously named import_author should be replaced by author_import_record_to_author\", same for build_query -> import_record_to_edition) and the interface declares load_author_import_records and check_cover_url_host. Caveat: 28 required tests over a broad import pipeline, pass_to_pass empty."),
 (23,"good_with_caveat","","medium","the interface declares LocalKeyAgent.ClientCertPool with its full signature and requirements name the assertion the single graded test makes (\"must surface the proxy's 'subsystem request failed' error\"). Caveat: that one test starts real auth and proxy processes and performs an SSO login, so a failure is as likely to be environmental as cognitive; pass_to_pass empty."),
 (24,"good","","high","the interface declares postsAPI.getRaw and postsAPI.getSummary with `caller, { pid }` inputs and the \"... or null\" contract, and requirements state the deleted-post rule (admin, moderator or author) that three of the five graded tests turn on."),
 (25,"uncertain","overly_strict_tests","low","the graded response test builds an expected Subsonic Share with Url \"http://localhost/p/ABC123\" and an Expires timestamp, and navidrome compares responses against committed .snapshots/ golden files. Requirements say only \"Response formats must comply with standard Subsonic specifications\" and \"apply reasonable defaults\" without naming a default expiry. Not resolved: whether the golden file is checked out at grading time, which decides whether the byte-exact comparison is actually graded."),
 (26,"good","","high","requirements pin both graded tests almost verbatim, including the private attribute name (\"an accessible values._vmap attribute whose iteration order reflects the insertion order; iter(values) must exactly match list(values._vmap.values())\") and the repr shape `odict_values([ScopedValue(...)])`, which also fixes the container as collections.OrderedDict. test_add_url_benchmark is in the test patch but not in fail_to_pass. Caveat: so tightly specified that a hint has little left to teach."),
 (27,"bad","overly_strict_tests","high","TestLoad asserts exact validation error values - errors.New(\"buffer capacity below 2 or above 10\"), errors.New(\"flush period below 2 minutes or greater than 5 minutes\"), errors.New(\"file not specified\"). Requirements state the conditions (\"outside 2-10\", \"outside 2m-5m\", \"enabled without a file\") and never the wording. The token screen surfaced all three literals."),
 (28,"good","","high","every graded assertion is toHaveTextContent on a string requirements give verbatim (\"Can't load this message\", \"<displayName> wants to verify\"). Both test helpers wrap the component in TileErrorBoundary, so throwing and rendering the string are equally acceptable; the thrown messages are only console-filtered, never asserted."),
 (29,"bad","overly_strict_tests","high","test_missing_cache_dir asserts the cache file's byte-exact content, `actual_cache == '{\"version\": 1}'`. The prompt says only \"storage of a version marker in the cache\" - it never gives the value 1 nor the serialization. The interface field is empty. get_cache_id's exact \"host:port\" form (with a trailing colon when there is no port) is likewise only implied."),
 (30,"bad","overly_strict_tests","high","two independent kills. Deductive: the test patch adds committed DOM snapshots for DeviceDetailHeading-test.tsx, DeviceDetails-test.tsx and SessionManagerTab-test.tsx, all three of which fail_to_pass names, so grading fixes every class name and nesting level of the rendered output. Separately underspecified: the graded tests query by exact data-testid values - device-detail-heading, device-heading-rename-cta, device-rename-input, device-rename-submit-cta, device-rename-cancel-cta, device-rename-error - while requirements say only \"should expose stable testing hooks (e.g., data-testid attributes)\" and name none of them."),
 (31,"good_with_caveat","","medium","all four signature changes the screen flags are stated: requirements bullet 35 gives the new callback verbatim as `varValidation(namespace, name string) error`, and MatchExpression plus MatchExpression.Match() are declared in the interface. Caveat: 71 required tests over an expression-language rewrite - determinate on paper, very large in practice."),
 (32,"good","","high","requirements give all three warning strings verbatim with their priority order, the disabled-flag formula, and the instruction to move the ResizeObserver mock into the shared jest setup that the test file relies on. Caveat: so tightly specified there is little for a hint to teach. NOTE: image family unrunnable."),
 (33,"good","","high","the one graded redaction case is stated verbatim (\"to map or other value types after stringification using Go's default formatting before regex replacement\"), and the reverse-proxy bullets give the Remote-User default, the CIDR whitelist semantics and the null auth object. RemoteAddr is a stdlib field."),
 (34,"bad","overly_strict_tests","high","the interface field declares getRegularRenewalNoticeText while every graded test imports and calls getCheckoutRenewNoticeText, which no prompt field names - the third instance in this set of the interface pinning one symbol while grading happens on another. NOTE: image family unrunnable."),
 (35,"bad","overly_strict_tests","high","the graded test \"should render as expected\" is expect(renderResult.baseElement).toMatchSnapshot() against a committed __snapshots__/UnverifiedSessionToast-test.tsx.snap that the test patch adds, and fail_to_pass names that test file. A DOM snapshot fixes every class name and nesting level; no prose prompt can pin that, so this is overly strict tests by construction rather than by suspicion."),
 (36,"good","","high","each guard is stated verbatim with its error string - a call lacking both mid and roomId must fail with [[error:invalid-data]], likewise chats.list without start/stop/page and users.getPrivateRoomId without uid. The tests pass undefined, so the `= {}` default destructuring follows from \"a call without ... must fail with\"."),
 (37,"bad","underspecified_prompt","high","the second graded test sets icon:bgColor to the invalid value 'teal' and requires the returned payload's icon:bgColor to be a member of getIconBackgrounds() - i.e. an invalid stored value must be replaced by a valid one. Nothing in the prompt says so. The requirements field is degenerate: six bullets about Node module-export mechanics (one part-Spanish) instead of the feature."),
 (38,"good_with_caveat","","medium","the interface field is a complete specification of the new package - Config with its three fields and SetDefaults, Buffer[T] with NewBuffer/Append/NewCursor/Close, Cursor[T] with Read/TryRead/Close, and the three error variables - and requirements add the defaults (64, 5m) and the finalizer requirement TestCursorFinalizer grades. Caveat: a from-scratch concurrent data structure, the second slowest in the set. The 78 token alarms were license-header words."),
 (39,"good_with_caveat","","medium","requirements state the four-element requirement tuple (name, version, type, path), the git-URL '#' syntax, the accepted type values and the galaxy.yml check that the graded tests exercise. Caveat: the interface field is empty, there are 18 required tests across 27 touched files, and it is the slowest instance in issue #261."),
]
RUNNABLE_UNKNOWN = {"element-hq/element-web", "tutao/tutanota"}
UNRUNNABLE = {"protonmail/webclients"}
PROVEN = {"qutebrowser/qutebrowser", "internetarchive/openlibrary", "future-architect/vuls",
          "navidrome/navidrome", "gravitational/teleport", "NodeBB/NodeBB"}

d = load_dataset('swebench_pro')
ids = (HERE / 'instances.txt').read_text().split()
screens = {r['instance_id']: r for r in json.loads((HERE / 'screens.json').read_text())}
rows = []
for idx, verdict, category, confidence, evidence in V:
  iid = ids[idx]
  i = d.require(iid)
  s = screens[iid]
  runnable = ("unrunnable" if i.repo in UNRUNNABLE
              else "proven" if i.repo in PROVEN else "untested")
  rows.append({
      "rank_in_issue_261": idx,
      "instance_id": iid,
      "repo": i.repo,
      "language": i.repo_language,
      "verdict": verdict,
      "category": category or None,
      "confidence": confidence,
      "evidence": evidence,
      "fail_to_pass": len(i.fail_to_pass),
      "pass_to_pass": len(i.pass_to_pass),
      "image_runnable": runnable,
      "screens": {
          "token": s["unpinned_tokens"],
          "token_but_present_in_repo": s.get("unpinned_but_present_in_repo", []),
          "diagonal": s["diagonal_conflicts"],
          "diagonal_units_compared": s["diagonal_units_compared"],
          "uncovered_symbols": s["uncovered_symbols"],
          "signature_changes": s["signature_changes"],
          "graded_snapshots": s.get("graded_snapshots", []),
      },
  })
(HERE / 'candidates.json').write_text(json.dumps(rows, indent=2) + "\n")
from collections import Counter
print(Counter(r["verdict"] for r in rows))
print(Counter(r["category"] for r in rows if r["category"]))
print("usable now:", [r["rank_in_issue_261"] for r in rows
                      if r["verdict"].startswith("good") and r["image_runnable"] != "unrunnable"])

#if DEBUG
import FitnessCore
import Foundation

/// DEBUG-only stand-in for the `/coach/*` endpoints while the demo data is loaded: a few proposals, a config history
/// and settings, with approve / reject / ask / rollback acting on this in-memory state.
final class DemoCoach: @unchecked Sendable {
    static let shared = DemoCoach()

    private let lock = NSLock()
    private var proposals: [[String: Any]] = []
    private var versions: [[String: Any]] = []
    private var mode = "manual"
    private var askPolls: [String: Int] = [:]

    private init() { seed() }

    static let pendingId = "demo-p1"

    var transport: CoachRepository.Transport {
        { [self] method, path, _, body in try respond(method, path, body) }
    }

    // MARK: Routing

    private func respond(_ method: String, _ path: String, _ body: Data?) throws -> Data {
        try lock.withLock { try handle(method, path, body) }
    }

    private func handle(_ method: String, _ path: String, _ body: Data?) throws -> Data {
        let parts = path.split(separator: "/").map(String.init)  // api v1 coach proposals <id> approve
        let payload = (body.flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any]) ?? [:]
        switch (method, parts.dropFirst(2).joined(separator: "/")) {
        case ("GET", "coach/proposals"): return json(["items": proposals])
        case ("GET", "coach/history"): return json(["versions": versions])
        case ("GET", "coach/settings"): return json(settings())
        case ("PUT", "coach/settings"):
            mode = payload["approval_mode"] as? String ?? mode
            return json(settings())
        case ("POST", "coach/ask"):
            let id = "demo-ask-\(proposals.count)"
            askPolls[id] = 0
            let scope = payload["scope"] as? String ?? "general"
            proposals.insert(proposal(id, "queued", trigger: ["type": "ask", "title": "Your question", "detail": scope, "ref": ["kind": (scope == "general" ? NSNull() : scope) as Any, "id": payload["ref_id"] ?? NSNull()]],
                                      question: payload["question"] as? String, minutesAgo: 0), at: 0)
            return json(proposals[0])
        case ("POST", "config/rollback/" + (parts.last ?? "")):
            let target = parts.last ?? ""
            rollback(to: target)
            return json(["active": ["version": target]])
        default: break
        }
        if parts.count >= 5, parts[2] == "coach", parts[3] == "proposals" {
            let id = parts[4]
            guard let i = proposals.firstIndex(where: { ($0["id"] as? String) == id }) else { throw APIError.http(404, nil) }
            switch (method, parts.count > 5 ? parts[5] : "") {
            case ("GET", ""):
                if let n = askPolls[id] {
                    askPolls[id] = n + 1
                    if n == 0 { proposals[i]["status"] = "running" }
                    else if n >= 2 { fillAnswer(i) }
                }
                var p = proposals[i]
                p["input_summary"] = ["lift": "Incline Dumbbell Press", "sessions_considered": 6, "e1rm_trend_4w_pct": -1.2]
                return json(p)
            case ("POST", "approve"):
                let ids = payload["change_ids"] as? [String]
                var changes = proposals[i]["changes"] as? [[String: Any]] ?? []
                var schedule = proposals[i]["schedule_changes"] as? [[String: Any]] ?? []
                var applied = 0
                for k in changes.indices {
                    let id = changes[k]["id"] as? String ?? ""
                    if (changes[k]["valid"] as? Bool) == true, ids?.contains(id) ?? true { changes[k]["status"] = "applied"; applied += 1 }
                    else if (changes[k]["valid"] as? Bool) == true { changes[k]["status"] = "rejected" }
                }
                for k in schedule.indices {
                    let id = schedule[k]["id"] as? String ?? ""
                    if (schedule[k]["valid"] as? Bool) == true, ids?.contains(id) ?? true { schedule[k]["status"] = "applied"; applied += 1 }
                    else if (schedule[k]["valid"] as? Bool) == true { schedule[k]["status"] = "rejected" }
                }
                proposals[i]["changes"] = changes
                proposals[i]["schedule_changes"] = schedule
                proposals[i]["status"] = applied == changes.count + schedule.count ? "applied" : "partially_applied"
                let next = "v3.3"
                proposals[i]["applied_config_version"] = next
                for k in versions.indices { versions[k]["active"] = false }
                versions.insert(["version": next, "created_at": iso(0), "author": "advisor", "active": true,
                                 "reason": proposals[i]["diagnosis"] as? String ?? "Coach proposal",
                                 "consultation_id": id, "diff": ["strength.rep_range": ["old": [7, 12], "new": [8, 12]]]], at: 0)
                return json(proposals[i])
            case ("POST", "reject"):
                proposals[i]["status"] = "rejected"
                return json(proposals[i])
            default: break
            }
        }
        throw APIError.http(404, nil)
    }

    // MARK: State

    private func settings() -> [String: Any] {
        ["approval_mode": mode, "api_key_configured": true, "dry_run": false, "calls_this_week": 2, "max_calls_per_week": 10]
    }

    private func rollback(to version: String) {
        guard let target = versions.first(where: { ($0["version"] as? String) == version }) else { return }
        for k in versions.indices { versions[k]["active"] = false }
        versions.insert(["version": "v3.4", "created_at": iso(0), "author": "user", "active": true,
                         "reason": "Rollback to \(version)", "diff": target["diff"] ?? [:]], at: 0)
    }

    private func fillAnswer(_ i: Int) {
        proposals[i]["status"] = "pending"
        proposals[i]["confidence"] = "medium"
        proposals[i]["diagnosis"] = "Your easy runs sit about 6 bpm under the ceiling, so the pace is fine. Nothing to change right now."
        proposals[i]["changes"] = [change("c1", "running.easy.hr_ceiling_bpm", "Easy run HR ceiling", 145, 147, "Small buffer for warm days.", "low")]
    }

    private func iso(_ minutesAgo: Int) -> String {
        ISO8601.string(Date().addingTimeInterval(-Double(minutesAgo) * 60))
    }

    private func change(_ id: String, _ param: String, _ label: String, _ from: Any, _ to: Any, _ reason: String,
                        _ risk: String, valid: Bool = true, error: String? = nil, status: String = "proposed") -> [String: Any] {
        var c: [String: Any] = ["id": id, "param": param, "label": label, "from": from, "to": to, "reason": reason,
                                "risk": risk, "valid": valid, "status": valid ? status : "invalid"]
        if let error { c["validation_error"] = error }
        return c
    }

    private func schedule(_ id: String, _ action: String, _ label: String, _ reason: String, _ risk: String, date: String,
                          toDate: String? = nil, valid: Bool = true, error: String? = nil) -> [String: Any] {
        var c: [String: Any] = ["id": id, "date": date, "action": action, "target": "session", "label": label, "reason": reason,
                                "risk": risk, "valid": valid, "status": valid ? "proposed" : "invalid"]
        if let toDate { c["to_date"] = toDate }
        if let error { c["validation_error"] = error }
        return c
    }

    private func proposal(_ id: String, _ status: String, trigger: [String: Any], question: String? = nil,
                          diagnosis: String? = nil, confidence: String? = nil, changes: [[String: Any]] = [],
                          schedule: [[String: Any]] = [],
                          overrides: [[String: Any]] = [], version: String? = nil, minutesAgo: Int, cost: Double? = nil,
                          notes: String? = nil) -> [String: Any] {
        var p: [String: Any] = ["id": id, "created_at": iso(minutesAgo), "status": status, "trigger": trigger,
                                "changes": changes, "session_overrides": overrides]
        if !schedule.isEmpty { p["schedule_changes"] = schedule }
        if let question { p["question"] = question }
        if let diagnosis { p["diagnosis"] = diagnosis }
        if let confidence { p["confidence"] = confidence }
        if let version { p["applied_config_version"] = version }
        if let cost { p["cost_usd"] = cost }
        if let notes { p["notes_for_user"] = notes }
        return p
    }

    private func seed() {
        proposals = [
            proposal(Self.pendingId, "pending",
                     trigger: ["type": "lift_stall", "title": "Incline Dumbbell Press has stalled", "detail": "No progress in 4 sessions at 7–12 reps.", "ref": ["kind": "lift", "id": "127"]],
                     diagnosis: "Four sessions without a new rep or load at the 7–12 range. Top sets sit at 9 reps on 26 kg, so the jump to the next dumbbell is too big for the reps you have left. A tighter range and a finer step should restart progress, and fatigue looks normal (RIR 1–2).",
                     confidence: "medium",
                     changes: [
                        change("c1", "strength.rep_range", "Rep range", [7, 12], [8, 12], "Start each exposure with more reps in reserve.", "low"),
                        change("c2", "strength.increments.dumbbell_increment_kg", "Dumbbell step", 2.5, 2.0, "A smaller step lets you progress by weight before the range tops out.", "low"),
                        change("c3", "strength.deload.load_pct", "Deload load drop", 10, 15, "Stalls here came after short deloads.", "medium"),
                        change("c4", "strength.rep_range_upper", "Upper rep limit", 12, 25, "Chase volume.", "high", valid: false, error: "Above the hard limit of 15 reps"),
                     ],
                     overrides: [["date": "2026-10-27", "session": "upper", "change": "Incline press: start at 24 kg for 3 x 8", "reason": "Rebuild reps at the lower step."]],
                     minutesAgo: 35, cost: 0.042, notes: "Your bench numbers are unaffected."),
            proposal("demo-p4", "pending",
                     trigger: ["type": "missed_session", "title": "Missed Wednesday's easy run", "detail": "No run logged on Wed 21 Oct."],
                     diagnosis: "The easy run on Wednesday did not happen, so the week is 8 km short. Friday's intervals come two days after a lower-body session; moving them one day later and trimming Sunday keeps the hard days apart.",
                     confidence: "medium",
                     changes: [change("c1", "running.easy.hr_ceiling_bpm", "Easy run HR ceiling", 145, 147, "Small buffer for warm days.", "low")],
                     schedule: [
                        schedule("s1", "move", "Move intervals Fri → Sat", "Legs are still heavy from Thursday's lower session; Saturday gives one more day.", "low", date: "2026-10-23", toDate: "2026-10-24"),
                        schedule("s2", "shorten", "Shorten Sunday long run to 9 km", "Weekly volume is already near the cap after the make-up.", "medium", date: "2026-10-25"),
                        schedule("s3", "replace", "Replace Wednesday easy run with intervals", "Catch up on quality work.", "high", date: "2026-10-21", valid: false,
                                 error: "Two hard sessions within 48 hours"),
                     ],
                     minutesAgo: 90, cost: 0.036),
            proposal("demo-p2", "applied",
                     trigger: ["type": "weekly_review", "title": "Weekly review", "detail": "Week 42"],
                     diagnosis: "Training load was steady. Interval pace held within 2 % of target, so the engine only needed a small tolerance tweak.",
                     confidence: "high",
                     changes: [change("c1", "running.intervals.pace_tolerance_pct", "Interval pace tolerance", 3, 4, "Heat on two sessions.", "low", status: "applied")],
                     version: "v3.2", minutesAgo: 60 * 24 * 6, cost: 0.031),
            proposal("demo-p3", "rejected",
                     trigger: ["type": "running_stagnation", "title": "Easy pace stagnating", "detail": "VDOT flat for 6 weeks."],
                     diagnosis: "VDOT has not moved in 6 weeks while easy volume stayed at 9 km.",
                     confidence: "low",
                     changes: [change("c1", "running.weekly_volume_cap_pct", "Weekly volume cap", 10, 15, "Grow volume faster.", "high", status: "rejected")],
                     minutesAgo: 60 * 24 * 12, cost: 0.029),
        ]
        versions = [
            ["version": "v3.2", "created_at": iso(60 * 24 * 6), "author": "advisor", "active": true, "consultation_id": "demo-p2",
             "reason": "Interval pace tolerance widened after two hot sessions.",
             "diff": ["running.intervals.pace_tolerance_pct": ["old": 3, "new": 4]]],
            ["version": "v3.1", "created_at": iso(60 * 24 * 20), "author": "user",
             "reason": "Weekly volume cap: floor of 18 km.",
             "diff": ["running.weekly_volume_floor_km": ["old": NSNull(), "new": 18], "strength.rep_range": ["old": [6, 10], "new": [7, 12]]],
             "outcome": ["verdict": "improved", "notes": "Weekly km up 22 % without a drop in readiness.", "metric_before": 14.2, "metric_after": 17.3]],
            ["version": "v3", "created_at": iso(60 * 24 * 60), "author": "seed", "reason": "Initial configuration", "diff": [String: Any]()],
        ]
    }

    private func json(_ object: Any) -> Data {
        (try? JSONSerialization.data(withJSONObject: object)) ?? Data("{}".utf8)
    }
}
#endif

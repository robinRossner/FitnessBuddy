#if DEBUG
import FitnessCore
import Foundation

/// DEBUG-only stand-in for the `/gyms`, `/variants` and `/weight-ladder` endpoints while the demo data is loaded.
/// Two gyms: Gym 3 (all machines set up, has history) and Gym 4 (current, just joined, partly set up).
final class DemoGyms: @unchecked Sendable {
    static let shared = DemoGyms()

    private let lock = NSLock()
    private var currentId = 4
    private var nextGymId = 5
    private var nextVariantId = 900
    private var gyms: [[String: Any]] = [
        ["id": 3, "name": "Gym 3", "source": "import"],
        ["id": 4, "name": "Gym 4", "source": "user"],
    ]
    /// gym id -> exercise id -> variant
    private var variants: [Int: [Int: [String: Any]]] = [:]

    private struct Ex { let id: Int; let name: String; let region: String; let program: Bool; let type: String }
    private let exercises: [Ex] = [
        Ex(id: 1, name: "Chest Press", region: "chest", program: true, type: "machine_stack"),
        Ex(id: 2, name: "Incline Bench Press", region: "chest", program: true, type: "smith"),
        Ex(id: 3, name: "Pec Deck", region: "chest", program: false, type: "machine_stack"),
        Ex(id: 4, name: "Lat Pulldown", region: "back", program: true, type: "cable"),
        Ex(id: 5, name: "Seated Row", region: "back", program: true, type: "machine_stack"),
        Ex(id: 6, name: "Shoulder Press", region: "shoulders", program: true, type: "machine_stack"),
        Ex(id: 7, name: "Lateral Raise", region: "shoulders", program: true, type: "cable"),
        Ex(id: 8, name: "Leg Press", region: "legs", program: true, type: "plate_loaded"),
        Ex(id: 9, name: "Leg Curl", region: "legs", program: true, type: "machine_stack"),
        Ex(id: 10, name: "Leg Extension", region: "legs", program: false, type: "machine_stack"),
        Ex(id: 11, name: "Triceps Pushdown", region: "arms", program: false, type: "cable"),
    ]

    private init() {
        func v(_ id: Int, _ name: String, _ type: String, _ ladder: [Double]) -> [String: Any] {
            ["id": id, "name": name, "equipment_type": type, "weight_ladder": ladder, "active": true, "sessions": 8]
        }
        let five = (1...15).map { Double($0) * 5 }
        let half = (1...30).map { Double($0) * 2.5 }
        variants[3] = [
            1: v(101, "Chest Press @ Gym 3", "machine_stack", five),
            2: v(102, "Incline Bench Press (Smith Machine) @ Gym 3", "smith", []),
            4: v(104, "Lat Pulldown @ Gym 3", "cable", half),
            5: v(105, "Seated Row @ Gym 3", "machine_stack", five),
            6: v(106, "Shoulder Press @ Gym 3", "machine_stack", five),
            7: v(107, "Lateral Raise @ Gym 3", "cable", half),
            8: v(108, "Leg Press @ Gym 3", "plate_loaded", []),
            9: v(109, "Leg Curl @ Gym 3", "machine_stack", half),
            10: v(110, "Leg Extension @ Gym 3", "machine_stack", half),
        ]
        variants[4] = [
            1: v(201, "Chest Press @ Gym 4", "machine_stack", (1...12).map { Double($0) * 7 }),
            2: v(202, "Incline Bench Press (Smith Machine) @ Gym 4", "smith", []),
            4: v(204, "Lat Pulldown @ Gym 4", "cable", half),
            8: v(208, "Leg Press @ Gym 4", "plate_loaded", []),
            9: v(209, "Leg Curl @ Gym 4", "machine_stack", five),
        ]
    }

    var transport: any APITransport {
        ClosureTransport { [self] method, path, _, body in try lock.withLock { try handle(method, path, body) } }
    }

    private func handle(_ method: String, _ path: String, _ body: Data?) throws -> Data {
        let parts = path.split(separator: "/").map(String.init)  // api v1 gyms 3 machines
        let payload = (body.flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any]) ?? [:]
        switch (method, parts.dropFirst(2).joined(separator: "/")) {
        case ("GET", "gyms"): return json(["current_gym_id": currentId, "gyms": gyms.map(summary)])
        case ("POST", "gyms"):
            let name = (payload["name"] as? String) ?? ""
            if gyms.contains(where: { ($0["name"] as? String)?.lowercased() == name.lowercased() }) {
                throw APIError.http(409, "{\"detail\": \"A gym named \(name) already exists.\"}")
            }
            let gym: [String: Any] = ["id": nextGymId, "name": name, "source": "user"]
            nextGymId += 1
            gyms.append(gym)
            return json(summary(gym))
        case ("PUT", "gyms/current"):
            currentId = payload["gym_id"] as? Int ?? currentId
            return json(["current_gym_id": currentId, "unmapped_count": unmapped(currentId)])
        case ("POST", "weight-ladder/preview"):
            let ladder = LadderRules.generate(start: payload["start"] as? Double ?? 5, step: payload["step"] as? Double ?? 5,
                                              count: payload["count"] as? Int ?? 15, extra: payload["extra"] as? [Double] ?? [])
            return json(["weight_ladder": ladder])
        default: break
        }
        if parts.count >= 4, parts[2] == "gyms", let id = Int(parts[3]) {
            guard let i = gyms.firstIndex(where: { $0["id"] as? Int == id }) else { throw APIError.http(404, nil) }
            switch (method, parts.count > 4 ? parts[4] : "") {
            case ("PATCH", ""):
                gyms[i]["name"] = payload["name"] as? String ?? gyms[i]["name"]
                return json(summary(gyms[i]))
            case ("DELETE", ""):
                if id == currentId { throw APIError.http(409, "{\"detail\": \"This is your current gym. Switch to another gym first.\"}") }
                if id == 3 { throw APIError.http(409, "{\"detail\": \"Gym 3 has logged sessions and can't be deleted.\"}") }
                gyms.remove(at: i)
                return Data()
            case ("GET", "machines"):
                return json(["gym": summary(gyms[i]), "items": items(for: id)])
            case ("POST", "machines"):
                let ex = payload["exercise_id"] as? Int ?? 0
                if variants[id]?[ex] != nil { throw APIError.http(409, "{\"detail\": \"This exercise already has a machine at this gym.\"}") }
                var v: [String: Any] = ["id": nextVariantId, "name": payload["name"] ?? "Machine", "active": true, "sessions": 0,
                                        "equipment_type": payload["equipment_type"] ?? "machine_stack",
                                        "weight_ladder": payload["weight_ladder"] ?? []]
                if let n = payload["notes"] { v["notes"] = n }
                nextVariantId += 1
                variants[id, default: [:]][ex] = v
                return json(items(for: id).first { $0["exercise_id"] as? Int == ex } ?? [:])
            default: break
            }
        }
        if parts.count == 4, parts[2] == "variants", let vid = Int(parts[3]), method == "PATCH" {
            for (g, byEx) in variants {
                for (ex, var v) in byEx where v["id"] as? Int == vid {
                    for key in ["name", "weight_ladder", "notes", "active"] { if let x = payload[key] { v[key] = x } }
                    variants[g]?[ex] = v
                    return json(v)
                }
            }
            throw APIError.http(404, nil)
        }
        throw APIError.http(404, nil)
    }

    private func summary(_ gym: [String: Any]) -> [String: Any] {
        let id = gym["id"] as? Int ?? 0
        var out = gym
        out["machine_count"] = variants[id]?.count ?? 0
        out["is_current"] = id == currentId
        out["created_at"] = "2026-10-01T09:00:00Z"
        return out
    }

    private func unmapped(_ gym: Int) -> Int {
        exercises.filter { $0.program && variants[gym]?[$0.id] == nil }.count
    }

    private func items(for gym: Int) -> [[String: Any]] {
        let other = gyms.first { ($0["id"] as? Int) != gym && !(variants[$0["id"] as? Int ?? 0] ?? [:]).isEmpty }
        return exercises.map { ex in
            var item: [String: Any] = ["exercise_id": ex.id, "exercise_name": ex.name, "body_region": ex.region,
                                       "in_program": ex.program, "equipment_type": ex.type]
            item["variant"] = variants[gym]?[ex.id] ?? NSNull()
            if let otherId = other?["id"] as? Int, let pv = variants[otherId]?[ex.id] {
                item["previous"] = ["gym_name": other?["name"] ?? "Gym", "variant_id": pv["id"] ?? 0,
                                    "last_top_set": ["weight_kg": 65.0, "reps": 9], "weight_ladder": pv["weight_ladder"] ?? []]
            }
            return item
        }
    }

    private func json(_ object: Any) -> Data {
        (try? JSONSerialization.data(withJSONObject: object)) ?? Data()
    }
}
#endif

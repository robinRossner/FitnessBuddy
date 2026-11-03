#if DEBUG
import FitnessCore
import Foundation

/// DEBUG-only stand-in for the `/gyms`, `/variants` and `/weight-ladder` endpoints while the demo data is loaded.
/// Two gyms: Gym 3 (the old gym: all machines set up, has history) and Gym 4 (current, joined recently, 3 of 14 machines
/// still to set up). Exercise and variant ids are those of `Samples/sync_pull.json` (Gym 4 variant = Gym 3 variant + 100),
/// so the machine lists agree with the exercise picker and the planned lifts.
final class DemoGyms: @unchecked Sendable {
    static let shared = DemoGyms()
    /// The gym the demo starts at (`DemoData` stores it as the local current gym).
    static let currentGymId = 4

    private let lock = NSLock()
    private var currentId = DemoGyms.currentGymId
    private var nextGymId = 5
    private var nextVariantId = 900
    private var gyms: [[String: Any]] = [
        ["id": 3, "name": "Gym 3", "source": "import"],
        ["id": 4, "name": "Gym 4", "source": "user"],
    ]
    /// gym id -> exercise id -> variant
    private var variants: [Int: [Int: [String: Any]]] = [:]

    private struct Ex {
        let id: Int, name: String, region: String, type: String
        /// Variant name at a gym ("Seated Row (Cable)"), the id of its Gym 3 variant and the last top set there.
        let variantName: String, gym3Variant: Int, top: (kg: Double, reps: Int)
        /// Not set up at Gym 4 yet.
        var needsSetup = false
    }
    private let exercises: [Ex] = [
        Ex(id: 31, name: "Chest Press", region: "chest", type: "machine_stack", variantName: "Chest Press", gym3Variant: 150, top: (65, 9)),
        Ex(id: 11, name: "Incline Bench Press", region: "chest", type: "smith", variantName: "Incline Bench Press (Smith Machine)", gym3Variant: 101, top: (45, 9)),
        Ex(id: 34, name: "Pec Deck", region: "chest", type: "machine_stack", variantName: "Pec Deck (Machine)", gym3Variant: 152, top: (50, 10), needsSetup: true),
        Ex(id: 32, name: "Lat Pulldown", region: "back", type: "cable", variantName: "Lat Pulldown (Cable)", gym3Variant: 151, top: (60, 10), needsSetup: true),
        Ex(id: 13, name: "Iso-Lateral Row", region: "back", type: "plate_loaded", variantName: "Iso-Lateral Row (Machine)", gym3Variant: 103, top: (60, 9)),
        Ex(id: 14, name: "Seated Row", region: "back", type: "cable", variantName: "Seated Row (Cable)", gym3Variant: 104, top: (50, 10)),
        Ex(id: 33, name: "Shoulder Press", region: "shoulders", type: "machine_stack", variantName: "Shoulder Press (Machine)", gym3Variant: 153, top: (35, 9), needsSetup: true),
        Ex(id: 12, name: "Lateral Raise", region: "shoulders", type: "cable", variantName: "Lateral Raise (Cable)", gym3Variant: 102, top: (10, 12)),
        Ex(id: 15, name: "Triceps Extension", region: "arms", type: "cable", variantName: "Triceps Extension (Cable)", gym3Variant: 105, top: (20, 12)),
        Ex(id: 21, name: "Lying Leg Curl", region: "legs", type: "machine_stack", variantName: "Lying Leg Curl (Machine)", gym3Variant: 130, top: (40, 10)),
        Ex(id: 22, name: "Squat", region: "legs", type: "smith", variantName: "Squat (Smith Machine)", gym3Variant: 131, top: (90, 8)),
        Ex(id: 23, name: "Leg Extension", region: "legs", type: "machine_stack", variantName: "Leg Extension (Machine)", gym3Variant: 132, top: (45, 12)),
        Ex(id: 24, name: "Calf Press on Seated Leg Press", region: "legs", type: "plate_loaded", variantName: "Calf Press on Seated Leg Press", gym3Variant: 133, top: (80, 14)),
        Ex(id: 25, name: "Cable Crunch", region: "core", type: "cable", variantName: "Cable Crunch", gym3Variant: 134, top: (35, 12)),
    ]

    private init() {
        func v(_ id: Int, _ name: String, _ type: String, _ ladder: [Double], sessions: Int) -> [String: Any] {
            ["id": id, "name": name, "equipment_type": type, "weight_ladder": ladder, "active": true, "sessions": sessions]
        }
        let five = (1...15).map { Double($0) * 5 }
        let half = (1...30).map { Double($0) * 2.5 }
        for ex in exercises {
            let ladder = ex.type == "cable" ? half : (ex.type == "machine_stack" ? five : [])
            variants[3, default: [:]][ex.id] = v(ex.gym3Variant, "\(ex.variantName) @ Gym 3", ex.type, ladder, sessions: 8)
            if !ex.needsSetup {
                // Chest Press is the one machine at Gym 4 with a custom ladder (the editor screen shows it).
                let ladder4 = ex.id == 31 ? (1...12).map { Double($0) * 7 } : ladder
                variants[4, default: [:]][ex.id] = v(ex.gym3Variant + 100, "\(ex.variantName) @ Gym 4", ex.type, ladder4, sessions: ex.id == 31 ? 0 : 2)
            }
        }
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
        exercises.filter { variants[gym]?[$0.id] == nil }.count
    }

    private func items(for gym: Int) -> [[String: Any]] {
        let other = gyms.first { ($0["id"] as? Int) != gym && !(variants[$0["id"] as? Int ?? 0] ?? [:]).isEmpty }
        return exercises.map { ex in
            var item: [String: Any] = ["exercise_id": ex.id, "exercise_name": ex.name, "body_region": ex.region,
                                       "in_program": true, "equipment_type": ex.type]
            item["variant"] = variants[gym]?[ex.id] ?? NSNull()
            if let otherId = other?["id"] as? Int, let pv = variants[otherId]?[ex.id] {
                item["previous"] = ["gym_name": other?["name"] ?? "Gym", "variant_id": pv["id"] ?? 0,
                                    "last_top_set": ["weight_kg": ex.top.kg, "reps": ex.top.reps], "weight_ladder": pv["weight_ladder"] ?? []]
            }
            return item
        }
    }

    private func json(_ object: Any) -> Data {
        (try? JSONSerialization.data(withJSONObject: object)) ?? Data()
    }
}
#endif

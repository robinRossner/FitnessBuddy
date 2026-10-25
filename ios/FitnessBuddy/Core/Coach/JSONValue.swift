import Foundation

/// Any JSON value. Coach `from` / `to` / `diff` values are free-form (a number, a rep range, a list of paces ...).
/// Object keys are kept exactly as sent (decode these with `CoachCoding.makeDecoder()`, not the snake-case one).
enum JSONValue: Codable, Sendable, Equatable {
    case null
    case bool(Bool)
    case number(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let b = try? c.decode(Bool.self) { self = .bool(b) }
        else if let d = try? c.decode(Double.self) { self = .number(d) }
        else if let s = try? c.decode(String.self) { self = .string(s) }
        else if let a = try? c.decode([JSONValue].self) { self = .array(a) }
        else if let o = try? c.decode([String: JSONValue].self) { self = .object(o) }
        else { throw DecodingError.dataCorruptedError(in: c, debugDescription: "Not a JSON value") }
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .null: try c.encodeNil()
        case .bool(let b): try c.encode(b)
        case .number(let d): try c.encode(d)
        case .string(let s): try c.encode(s)
        case .array(let a): try c.encode(a)
        case .object(let o): try c.encode(o)
        }
    }

    var isNull: Bool { if case .null = self { true } else { false } }
    var doubleValue: Double? { if case .number(let d) = self { d } else { nil } }
    var arrayValue: [JSONValue]? { if case .array(let a) = self { a } else { nil } }
}

/// Human formatting of config values, with units implied by the parameter path (`...pct` -> %, `..._km` -> km, ...).
enum CoachValueFormat {
    /// "7–12", "10 %", "4 km", "1, 2, 3", "on", "easy: 5:30, long: 5:50", "none".
    static func text(_ value: JSONValue, path: String? = nil) -> String {
        let unit = path.flatMap(unit(forPath:))
        switch value {
        case .null: return "none"
        case .bool(let b): return b ? "on" : "off"
        case .string(let s): return s.isEmpty ? "empty" : s
        case .number(let d): return number(d, unit: unit)
        case .array(let items):
            if items.count == 2, let a = items[0].doubleValue, let b = items[1].doubleValue {
                return number(a) + "–" + number(b, unit: unit)  // [7, 12] -> 7–12
            }
            return list(items, path: path)
        case .object(let o):
            if o.isEmpty { return "empty" }
            let shown = o.keys.sorted().prefix(4).map { key in
                "\(key.replacingOccurrences(of: "_", with: " ")): \(text(o[key] ?? .null, path: path.map { $0 + "." + key } ?? key))"
            }
            return shown.joined(separator: ", ") + (o.count > 4 ? " +\(o.count - 4)" : "")
        }
    }

    /// Compact list: "1, 2, 3" (at most 6 items, then "+n").
    private static func list(_ items: [JSONValue], path: String?) -> String {
        if items.isEmpty { return "empty" }
        let shown = items.prefix(6).map { text($0, path: path) }
        return shown.joined(separator: ", ") + (items.count > 6 ? " +\(items.count - 6)" : "")
    }

    /// Unit implied by the last path segment.
    static func unit(forPath path: String) -> String? {
        let last = path.split(separator: ".").last.map(String.init)?.lowercased() ?? path.lowercased()
        if last == "pct" || last.hasSuffix("_pct") { return "%" }
        if last.hasSuffix("_km") || last == "km" { return "km" }
        if last.hasSuffix("_kg") || last == "kg" { return "kg" }
        if last.hasSuffix("_min") { return "min" }
        if last.hasSuffix("_m") { return "m" }
        if last.hasSuffix("_s") || last.hasSuffix("_sec") { return "s" }
        if last.hasSuffix("_bpm") { return "bpm" }
        return nil
    }

    static func number(_ d: Double, unit: String? = nil) -> String {
        let n = (d.rounded() == d && abs(d) < 1e15) ? String(Int(d)) : Fmt.number(d, maxFraction: 2)
        guard let unit else { return n }
        return unit == "%" ? n + " %" : n + " " + unit
    }

    /// "old → new".
    static func change(from: JSONValue, to: JSONValue, path: String?) -> String {
        text(from, path: path) + " → " + text(to, path: path)
    }
}

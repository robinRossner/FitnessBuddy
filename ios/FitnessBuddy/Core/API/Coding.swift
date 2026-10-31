import Foundation

/// ISO-8601 helpers tolerant of fractional seconds, `+00:00` offsets and naive (zone-less) timestamps.
enum ISO8601 {
    // ISO8601DateFormatter is documented as thread-safe.
    nonisolated(unsafe) private static let withFraction: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    nonisolated(unsafe) private static let plain: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    static func parse(_ raw: String) -> Date? {
        var s = raw.trimmingCharacters(in: .whitespaces)
        guard !s.isEmpty else { return nil }
        if s.count == 10 { s += "T00:00:00Z" }  // date only
        else if !hasZone(s) { s += "Z" }          // naive => UTC
        return withFraction.date(from: s) ?? plain.date(from: s)
    }

    static func string(_ date: Date) -> String { withFraction.string(from: date) }

    private static func hasZone(_ s: String) -> Bool {
        if s.hasSuffix("Z") || s.hasSuffix("z") { return true }
        // trailing +HH:MM / -HH:MM / +HHMM
        guard let tIndex = s.firstIndex(of: "T") else { return false }
        let tail = s[tIndex...]
        return tail.contains("+") || tail.dropFirst().contains("-")
    }
}

enum APICoding {
    static func makeDecoder() -> JSONDecoder {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        d.dateDecodingStrategy = .custom { decoder in
            let c = try decoder.singleValueContainer()
            let s = try c.decode(String.self)
            guard let date = ISO8601.parse(s) else {
                throw DecodingError.dataCorruptedError(in: c, debugDescription: "Unparseable date: \(s)")
            }
            return date
        }
        return d
    }

    /// Same dates, but object keys are kept exactly as sent. For payloads with free-form JSON (coach config diffs,
    /// where a key such as `running.easy_pace` must not become `running.easyPace`); DTOs spell their keys out.
    static func makeRawKeyDecoder() -> JSONDecoder {
        let d = makeDecoder()
        d.keyDecodingStrategy = .useDefaultKeys
        return d
    }

    static func makeEncoder() -> JSONEncoder {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        e.dateEncodingStrategy = .custom { date, encoder in
            var c = encoder.singleValueContainer()
            try c.encode(ISO8601.string(date))
        }
        e.outputFormatting = [.sortedKeys]
        return e
    }
}

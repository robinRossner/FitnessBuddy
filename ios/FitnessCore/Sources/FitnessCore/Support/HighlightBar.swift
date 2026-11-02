import Foundation

/// One comparison bar of a highlight card (Health style).
public struct HighlightBar: Identifiable, Equatable, Sendable {
    public var value: String
    public var unit: String
    /// "Last week", "8-week average".
    public var label: String
    /// 0...1 bar length relative to the longest bar.
    public var fraction: Double
    /// The current value: drawn in the card colour, the comparison in grey.
    public var highlighted: Bool
    public var id: String { label }
}

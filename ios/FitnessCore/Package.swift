// swift-tools-version: 6.0
import PackageDescription

// FitnessCore: the app's Foundation-only code (API client + DTOs, coding, stats/coach/gym logic, transforms,
// formatting). No SwiftUI / SwiftData / UIKit, so it builds for macOS too and `swift test` runs without the app host.
let package = Package(
    name: "FitnessCore",
    platforms: [.iOS(.v18), .macOS(.v15)],
    products: [
        .library(name: "FitnessCore", targets: ["FitnessCore"]),
    ],
    targets: [
        .target(name: "FitnessCore"),
        .testTarget(
            name: "FitnessCoreTests",
            dependencies: ["FitnessCore"],
            // `Resources/examples` is a symlink to docs/api/examples (the backend's generated API examples). `.process`
            // follows it and copies the JSON files flat into the bundle root (`.copy` would copy the dangling link).
            resources: [.process("Resources/examples")]
        ),
    ]
)

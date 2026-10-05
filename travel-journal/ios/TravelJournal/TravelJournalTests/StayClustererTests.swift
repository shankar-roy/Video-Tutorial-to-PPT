import XCTest
@testable import TravelJournal

final class StayClustererTests: XCTestCase {
    private let utc = TimeZone(secondsFromGMT: 0)!
    private var kmPerDegree: Double { 6371.0088 * Double.pi / 180 }

    func testGapAndDistanceSplitStays() {
        let same = (lat: 37.77, lon: -122.42)
        let first = photo("a", at(8), same.lat, same.lon)
        let later = photo("b", at(8).addingTimeInterval(7 * 3600), same.lat, same.lon)
        let north = same.lat + (40 / kmPerDegree)
        let moved = photo("c", later.takenAt.addingTimeInterval(3600), north, same.lon)
        XCTAssertGreaterThan(StayClusterer.haversineKm(same.lat, same.lon, north, same.lon), 30)
        let stays = StayClusterer.cluster(photos: [moved, later, first], timeZone: utc)
        XCTAssertEqual(stays.count, 3)
        XCTAssertTrue(stays[0].id.hasPrefix("2024-06-01T08:00:00|"))
    }

    func testSixHoursAndThirtyKilometresStayTogether() {
        let start = photo("a", at(8), 37.77, -122.42)
        let exactlySix = photo("b", at(8).addingTimeInterval(6 * 3600), 37.77, -122.42)
        let north = 37.77 + (30 / kmPerDegree)
        XCTAssertEqual(StayClusterer.haversineKm(37.77, -122.42, north, -122.42), 30, accuracy: 0.05)
        let nearby = photo("c", at(9), north, -122.42)
        XCTAssertEqual(StayClusterer.cluster(photos: [start, exactlySix], timeZone: utc).count, 1)
        let stays = StayClusterer.cluster(photos: [start, nearby], timeZone: utc)
        XCTAssertEqual(stays.count, 1)
        XCTAssertEqual(stays[0].latitude, (37.77 + north) / 2, accuracy: 0.0001)
    }

    func testHomeIsThePlaceWithTheMostNights() {
        var points: [PhotoPoint] = []
        for (day, hour) in [(1, 23), (2, 1), (3, 22), (5, 23), (4, 12), (4, 13)] {
            points.append(photo("h\(day)\(hour)", at(hour, day: day), 37.77, -122.42))
        }
        for index in 0..<8 {
            points.append(photo("a\(index)", at(12, day: 10).addingTimeInterval(Double(index) * 3600), 34.05, -118.24))
        }
        points.append(photo("night", at(23, day: 10), 34.05, -118.24))
        let proposal = StayClusterer.proposeHome(stays: StayClusterer.cluster(photos: points, timeZone: utc), timeZone: utc)
        XCTAssertNotNil(proposal)
        XCTAssertGreaterThanOrEqual(proposal?.nightCount ?? 0, 3)
        XCTAssertEqual(proposal?.latitude ?? 0, 37.77, accuracy: 0.5)
        XCTAssertGreaterThanOrEqual(proposal?.radiusKm ?? 0, 10)
    }

    func testNightKeyUsesWallClock() {
        XCTAssertEqual(StayClusterer.nightKey(for: at(22), timeZone: utc), "2024-06-01")
        XCTAssertEqual(StayClusterer.nightKey(for: at(5, day: 2), timeZone: utc), "2024-06-01")
        XCTAssertNil(StayClusterer.nightKey(for: at(6), timeZone: utc))
        XCTAssertNil(StayClusterer.proposeHome(stays: [], timeZone: utc))
    }

    func testCandidatePhotosSkipBurstsAndCapAtSix() {
        var frames = [
            photo("burst", at(9), 38.72, -9.14),
            photo("burst-2", at(9, minute: 1), 38.7201, -9.1401),
        ]
        for hour in 10..<18 {
            frames.append(photo("h\(hour)", at(hour), 38.72, -9.14))
        }
        let chosen = StayClusterer.selectCandidates(frames)
        XCTAssertEqual(chosen.count, 6)
        XCTAssertEqual(chosen[0].assetId, "burst")
        XCTAssertFalse(chosen.contains { $0.assetId == "burst-2" })
    }

    private func at(_ hour: Int, day: Int = 1, minute: Int = 0) -> Date {
        var parts = DateComponents()
        parts.calendar = Calendar(identifier: .gregorian)
        parts.timeZone = utc
        parts.year = 2024
        parts.month = 6
        parts.day = day
        parts.hour = hour
        parts.minute = minute
        return parts.date!
    }

    private func photo(_ asset: String, _ when: Date, _ lat: Double, _ lon: Double) -> PhotoPoint {
        PhotoPoint(assetId: asset, takenAt: when, latitude: lat, longitude: lon)
    }
}

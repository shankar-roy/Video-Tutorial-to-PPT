import Foundation

/// On-device photo clustering. Keep this in step with
/// `backend/app/clustering.py`: a new stay starts when the gap since the
/// previous located photo is over 6 hours, or the move is over 30 km. Home is
/// the 50 km cluster with the most nights. A night photo is taken at 22:00 or
/// later (that date) or before 06:00 (the previous date).
enum StayClusterer {
    static let gapSeconds: TimeInterval = 6 * 60 * 60
    static let splitKilometers = 30.0
    static let homeClusterKilometers = 50.0
    static let burstSeconds: TimeInterval = 120
    static let burstKilometers = 0.1
    static let maxCandidatePhotos = 6
    private static let earthRadiusKm = 6371.0088

    static func cluster(photos: [PhotoPoint], timeZone: TimeZone = .current) -> [StayCluster] {
        let located = photos.sorted {
            if $0.takenAt != $1.takenAt { return $0.takenAt < $1.takenAt }
            return $0.assetId < $1.assetId
        }
        guard var current = located.first.map({ [$0] }) else { return [] }
        var groups: [[PhotoPoint]] = []
        for photo in located.dropFirst() {
            let previous = current[current.count - 1]
            let gap = photo.takenAt.timeIntervalSince(previous.takenAt)
            let distance = haversineKm(previous.latitude, previous.longitude, photo.latitude, photo.longitude)
            if gap > gapSeconds || distance > splitKilometers {
                groups.append(current)
                current = [photo]
            } else {
                current.append(photo)
            }
        }
        groups.append(current)
        return groups.map { group in
            let latitude = group.map(\.latitude).reduce(0, +) / Double(group.count)
            let longitude = group.map(\.longitude).reduce(0, +) / Double(group.count)
            return StayCluster(
                id: clientId(start: group[0].takenAt, latitude: latitude, longitude: longitude, timeZone: timeZone),
                startAt: group[0].takenAt,
                endAt: group[group.count - 1].takenAt,
                latitude: latitude,
                longitude: longitude,
                placeName: "",
                photos: group
            )
        }
    }

    static func proposeHome(stays: [StayCluster], timeZone: TimeZone = .current) -> HomeProposal? {
        guard !stays.isEmpty else { return nil }
        let ordered = stays.sorted {
            if $0.photos.count != $1.photos.count { return $0.photos.count > $1.photos.count }
            if $0.startAt != $1.startAt { return $0.startAt < $1.startAt }
            return $0.id < $1.id
        }
        var clusters: [[StayCluster]] = []
        for stay in ordered {
            if let index = clusters.firstIndex(where: { cluster in
                let centroid = centroid(of: cluster)
                return haversineKm(stay.latitude, stay.longitude, centroid.latitude, centroid.longitude) <= homeClusterKilometers
            }) {
                clusters[index].append(stay)
            } else {
                clusters.append([stay])
            }
        }
        guard let best = clusters.max(by: { score($0, timeZone: timeZone) < score($1, timeZone: timeZone) }) else {
            return nil
        }
        let center = centroid(of: best)
        let farthest = best.map { haversineKm(center.latitude, center.longitude, $0.latitude, $0.longitude) }.max() ?? 0
        let nights = nightCount(best, timeZone: timeZone)
        let photos = best.reduce(0) { $0 + $1.photos.count }
        return HomeProposal(
            latitude: center.latitude,
            longitude: center.longitude,
            radiusKm: min(80, max(10, farthest + 5)),
            nightCount: nights,
            photoCount: photos
        )
    }

    static func selectCandidates(_ photos: [PhotoPoint]) -> [PhotoPoint] {
        let ordered = photos.sorted {
            if $0.takenAt != $1.takenAt { return $0.takenAt < $1.takenAt }
            return $0.assetId < $1.assetId
        }
        var kept: [PhotoPoint] = []
        for photo in ordered {
            if let last = kept.last {
                let gap = photo.takenAt.timeIntervalSince(last.takenAt)
                let distance = haversineKm(last.latitude, last.longitude, photo.latitude, photo.longitude)
                if gap < burstSeconds && distance < burstKilometers {
                    continue
                }
            }
            kept.append(photo)
        }
        guard kept.count > maxCandidatePhotos else { return kept }
        return spreadIndices(count: kept.count, limit: maxCandidatePhotos).map { kept[$0] }
    }

    static func haversineKm(_ lat1: Double, _ lon1: Double, _ lat2: Double, _ lon2: Double) -> Double {
        let phi1 = lat1 * .pi / 180
        let phi2 = lat2 * .pi / 180
        let dPhi = (lat2 - lat1) * .pi / 180
        let dLambda = (lon2 - lon1) * .pi / 180
        let a = sin(dPhi / 2) * sin(dPhi / 2) + cos(phi1) * cos(phi2) * sin(dLambda / 2) * sin(dLambda / 2)
        return 2 * earthRadiusKm * asin(min(1, sqrt(a)))
    }

    static func examplePhotos(timeZone: TimeZone = .current) -> [PhotoPoint] {
        var points: [PhotoPoint] = []
        let calendar = gregorian(timeZone)
        for (day, hour) in [(1, 23), (2, 1), (3, 22), (5, 23), (4, 12)] {
            points.append(PhotoPoint(assetId: "sf-\(day)-\(hour)", takenAt: date(2024, 6, day, hour, calendar: calendar), latitude: 37.77, longitude: -122.42))
        }
        for hour in [18, 23] {
            points.append(PhotoPoint(assetId: "lis-13-\(hour)", takenAt: date(2024, 6, 13, hour, calendar: calendar), latitude: 38.72, longitude: -9.14))
        }
        points.append(PhotoPoint(assetId: "lis-14", takenAt: date(2024, 6, 14, 15, calendar: calendar), latitude: 38.71, longitude: -9.14))
        return points
    }

    static func clientId(start: Date, latitude: Double, longitude: Double, timeZone: TimeZone) -> String {
        let formatter = DateFormatter()
        formatter.calendar = gregorian(timeZone)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = timeZone
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return "\(formatter.string(from: start))|\(String(format: "%.3f", latitude))|\(String(format: "%.3f", longitude))"
    }

    private static func nightCount(_ stays: [StayCluster], timeZone: TimeZone) -> Int {
        var nights = Set<String>()
        for stay in stays {
            for photo in stay.photos {
                if let key = nightKey(for: photo.takenAt, timeZone: timeZone) {
                    nights.insert(key)
                }
            }
        }
        return nights.count
    }

    static func nightKey(for date: Date, timeZone: TimeZone) -> String? {
        let calendar = gregorian(timeZone)
        let hour = calendar.component(.hour, from: date)
        let start = calendar.startOfDay(for: date)
        let formatter = DateFormatter()
        formatter.calendar = calendar
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = timeZone
        formatter.dateFormat = "yyyy-MM-dd"
        if hour >= 22 {
            return formatter.string(from: start)
        }
        if hour < 6, let previous = calendar.date(byAdding: .day, value: -1, to: start) {
            return formatter.string(from: previous)
        }
        return nil
    }

    private static func score(_ cluster: [StayCluster], timeZone: TimeZone) -> (Int, Int, TimeInterval) {
        let earliest = cluster.map(\.startAt).min() ?? .distantFuture
        return (nightCount(cluster, timeZone: timeZone), cluster.reduce(0) { $0 + $1.photos.count }, -earliest.timeIntervalSince1970)
    }

    private static func centroid(of stays: [StayCluster]) -> (latitude: Double, longitude: Double) {
        let latitude = stays.map(\.latitude).reduce(0, +) / Double(stays.count)
        let longitude = stays.map(\.longitude).reduce(0, +) / Double(stays.count)
        return (latitude, longitude)
    }

    private static func spreadIndices(count: Int, limit: Int) -> [Int] {
        if count <= limit { return Array(0..<count) }
        var chosen: [Int] = []
        if limit > 1 {
            for step in 0..<limit {
                let index = Int((Double(step) * Double(count - 1) / Double(limit - 1)).rounded())
                if !chosen.contains(index) { chosen.append(index) }
            }
        }
        var cursor = 0
        while chosen.count < limit && cursor < count {
            if !chosen.contains(cursor) { chosen.append(cursor) }
            cursor += 1
        }
        return chosen.sorted()
    }

    private static func gregorian(_ timeZone: TimeZone) -> Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        return calendar
    }

    private static func date(_ year: Int, _ month: Int, _ day: Int, _ hour: Int, calendar: Calendar) -> Date {
        var parts = DateComponents()
        parts.calendar = calendar
        parts.timeZone = calendar.timeZone
        parts.year = year
        parts.month = month
        parts.day = day
        parts.hour = hour
        return calendar.date(from: parts) ?? Date()
    }
}

struct PhotoPoint: Codable, Equatable, Identifiable {
    var assetId: String
    var takenAt: Date
    var latitude: Double
    var longitude: Double
    var id: String { assetId }
}

struct StayCluster: Codable, Equatable, Identifiable {
    var id: String
    var startAt: Date
    var endAt: Date
    var latitude: Double
    var longitude: Double
    var placeName: String
    var photos: [PhotoPoint]
}

struct HomeProposal: Equatable {
    var latitude: Double
    var longitude: Double
    var radiusKm: Double
    var nightCount: Int
    var photoCount: Int
}

struct HomeConfirmation: Codable, Equatable {
    var city: String
    var latitude: Double
    var longitude: Double
    var radiusKm: Double
}

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../api/client.dart';
import '../models/tool_spec.dart';

/// Where the backend lives and how to authenticate. The token is kept in Android Keystore-backed secure storage.
class ServerSettings {
  const ServerSettings({this.baseUrl = '', this.token = ''});
  final String baseUrl;
  final String token;

  bool get configured => baseUrl.isNotEmpty && token.isNotEmpty;
}

/// Storage seam so widget tests can run without platform channels.
abstract class SettingsStore {
  Future<ServerSettings> load();
  Future<void> save(ServerSettings s);
}

class SecureSettingsStore implements SettingsStore {
  static const _s = FlutterSecureStorage();

  @override
  Future<ServerSettings> load() async => ServerSettings(
        baseUrl: await _s.read(key: 'base_url') ?? '',
        token: await _s.read(key: 'api_token') ?? '',
      );

  @override
  Future<void> save(ServerSettings s) async {
    await _s.write(key: 'base_url', value: s.baseUrl);
    await _s.write(key: 'api_token', value: s.token);
  }
}

final settingsStoreProvider = Provider<SettingsStore>((ref) => SecureSettingsStore());

class SettingsNotifier extends AsyncNotifier<ServerSettings> {
  @override
  Future<ServerSettings> build() => ref.read(settingsStoreProvider).load();

  Future<void> save(ServerSettings s) async {
    await ref.read(settingsStoreProvider).save(s);
    state = AsyncData(s);
  }
}

final settingsProvider = AsyncNotifierProvider<SettingsNotifier, ServerSettings>(SettingsNotifier.new);

/// Null until the server is configured in Settings.
final clientProvider = Provider<ApiClient?>((ref) {
  final s = ref.watch(settingsProvider).value;
  if (s == null || !s.configured) return null;
  return ApiClient(baseUrl: s.baseUrl, token: s.token);
});

ApiClient requireClient(Ref ref) {
  final c = ref.watch(clientProvider);
  if (c == null) throw ApiException('Server not configured - open Settings');
  return c;
}

final toolsProvider = FutureProvider<List<ToolSpec>>((ref) async {
  final raw = await requireClient(ref).tools();
  return raw.map(ToolSpec.fromJson).toList();
});

final statusProvider = FutureProvider<Map<String, dynamic>>((ref) async {
  final r = await requireClient(ref).call('hub_status');
  if (r.isError) throw ApiException(r.error ?? 'hub_status failed');
  return (r.result as Map).cast<String, dynamic>();
});

final trackerProvider = FutureProvider.autoDispose<Map<String, dynamic>>((ref) async {
  final r = await requireClient(ref).call('tracker_report');
  if (r.isError || r.result is! Map) throw ApiException(r.error ?? 'tracker_report failed');
  return (r.result as Map).cast<String, dynamic>();
});

final runsProvider = FutureProvider<List<Map<String, dynamic>>>((ref) => requireClient(ref).runs());

final runDetailProvider = FutureProvider.family<Map<String, dynamic>, String>((ref, runId) => requireClient(ref).run(runId, tail: 500));

/// Filters for the Jobs tab.
class JobFilter {
  const JobFilter({this.status, this.minScore});
  final String? status;
  final double? minScore;

  @override
  bool operator ==(Object other) => other is JobFilter && other.status == status && other.minScore == minScore;
  @override
  int get hashCode => Object.hash(status, minScore);
}

final jobsProvider = FutureProvider.family<Map<String, dynamic>, JobFilter>((ref, f) async {
  final r = await requireClient(ref).call('list_jobs', {
    'limit': 100,
    if (f.status != null) 'status': f.status,
    if (f.minScore != null) 'min_score': f.minScore,
  });
  if (r.isError) throw ApiException(r.error ?? 'list_jobs failed');
  return (r.result as Map).cast<String, dynamic>();
});

final contactsProvider = FutureProvider<List<Map<String, dynamic>>>((ref) async {
  final r = await requireClient(ref).call('list_hr_contacts', {'limit': 200});
  final res = r.result;
  if (res is Map && res['contacts'] is List) return (res['contacts'] as List).cast<Map<String, dynamic>>();
  if (res is List) return res.cast<Map<String, dynamic>>();
  return const [];
});

/// Run queue (running now / waiting / limit). Screens that show it refresh it on a timer.
final queueProvider = FutureProvider.autoDispose<Map<String, dynamic>>((ref) => requireClient(ref).queue());

/// Linked accounts: LinkedIn / Naukri / Indeed sessions and Gmail.
final accountsProvider = FutureProvider.autoDispose<Map<String, dynamic>>((ref) => requireClient(ref).accounts());

/// Alarms (clock-time schedule) plus the task catalogue.
final alarmsProvider = FutureProvider.autoDispose<Map<String, dynamic>>((ref) async {
  final r = await requireClient(ref).call('alarm_list');
  if (r.isError) throw ApiException(r.error ?? 'alarm_list failed');
  return (r.result as Map).cast<String, dynamic>();
});

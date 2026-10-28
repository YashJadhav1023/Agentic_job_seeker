import 'dart:convert';

import 'package:http/http.dart' as http;

/// Error returned by the Agentic Job Seeker API (HTTP error or transport failure).
class ApiException implements Exception {
  ApiException(this.message, {this.status});
  final String message;
  final int? status;

  bool get unauthorized => status == 401;

  @override
  String toString() => status == null ? message : 'HTTP $status: $message';
}

/// Result of running one hub tool.
class ToolResult {
  ToolResult({required this.tool, required this.isError, required this.result});
  final String tool;
  final bool isError;
  final dynamic result;

  /// Background tools (apply_jobs, autopilot_run, ...) return a run id to poll.
  String? get runId => result is Map ? result['run_id'] as String? : null;

  String? get error => result is Map ? result['error']?.toString() : null;

  factory ToolResult.fromJson(Map<String, dynamic> j) =>
      ToolResult(tool: j['tool'] as String, isError: j['is_error'] == true, result: j['result']);
}

/// Thin typed client over the FastAPI backend (hub/api.py). All calls send the bearer token.
class ApiClient {
  ApiClient({required String baseUrl, required this.token, http.Client? httpClient})
      : baseUrl = baseUrl.replaceAll(RegExp(r'/+$'), ''),
        _http = httpClient ?? http.Client();

  final String baseUrl;
  final String token;
  final http.Client _http;

  static const _timeout = Duration(seconds: 300); // search_jobs / find_company_emails can be slow

  Map<String, String> get _headers => {
        'Authorization': 'Bearer $token',
        'Accept': 'application/json',
      };

  Uri _u(String path, [Map<String, String>? q]) => Uri.parse('$baseUrl$path').replace(queryParameters: q);

  Future<dynamic> _send(Future<http.Response> Function() req) async {
    final http.Response r;
    try {
      r = await req().timeout(_timeout);
    } on Exception catch (e) {
      throw ApiException('Cannot reach server: $e');
    }
    dynamic body;
    try {
      body = r.body.isEmpty ? null : jsonDecode(utf8.decode(r.bodyBytes));
    } on FormatException {
      body = r.body;
    }
    if (r.statusCode >= 400) {
      final detail = body is Map && body['detail'] != null ? body['detail'].toString() : r.reasonPhrase ?? 'error';
      throw ApiException(detail, status: r.statusCode);
    }
    return body;
  }

  Future<Map<String, dynamic>> health() async =>
      (await _send(() => _http.get(_u('/health')))) as Map<String, dynamic>;

  Future<List<Map<String, dynamic>>> tools() async {
    final j = await _send(() => _http.get(_u('/api/v1/tools'), headers: _headers)) as Map<String, dynamic>;
    return (j['tools'] as List).cast<Map<String, dynamic>>();
  }

  Future<ToolResult> call(String tool, [Map<String, dynamic> args = const {}]) async {
    final j = await _send(() => _http.post(_u('/api/v1/tools/$tool'),
        headers: {..._headers, 'Content-Type': 'application/json'}, body: jsonEncode(args)));
    return ToolResult.fromJson(j as Map<String, dynamic>);
  }

  Future<List<Map<String, dynamic>>> runs({int limit = 30}) async {
    final j = await _send(() => _http.get(_u('/api/v1/runs', {'limit': '$limit'}), headers: _headers))
        as Map<String, dynamic>;
    return (j['runs'] as List).cast<Map<String, dynamic>>();
  }

  Future<Map<String, dynamic>> run(String id, {int tail = 120}) async =>
      (await _send(() => _http.get(_u('/api/v1/runs/${Uri.encodeComponent(id)}', {'tail': '$tail'}),
          headers: _headers))) as Map<String, dynamic>;

  Future<Map<String, dynamic>> profile() async =>
      (await _send(() => _http.get(_u('/api/v1/profile'), headers: _headers))) as Map<String, dynamic>;

  Future<Map<String, dynamic>> saveProfile(String yaml) async => (await _send(() => _http.put(_u('/api/v1/profile'),
      headers: {..._headers, 'Content-Type': 'application/x-yaml'}, body: utf8.encode(yaml)))) as Map<String, dynamic>;

  /// Upload the resume. By default the server refreshes profile skills from it; [replaceSearch] also lets it pick
  /// the job titles to search, [syncNaukri] pushes it to the Naukri profile.
  Future<Map<String, dynamic>> uploadResume(List<int> bytes, String filename,
      {bool updateProfile = true, bool replaceSearch = false, bool syncNaukri = false}) async {
    final req = http.MultipartRequest(
        'POST',
        _u('/api/v1/resume', {
          'update_profile': '$updateProfile',
          'replace_search': '$replaceSearch',
          'sync_naukri': '$syncNaukri',
        }))
      ..headers.addAll(_headers)
      ..files.add(http.MultipartFile.fromBytes('file', bytes, filename: filename));
    return (await _send(() async => http.Response.fromStream(await _http.send(req)))) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> resumeInfo() async =>
      (await _send(() => _http.get(_u('/api/v1/resume'), headers: _headers))) as Map<String, dynamic>;

  Future<Map<String, dynamic>> accounts() async =>
      (await _send(() => _http.get(_u('/api/v1/accounts'), headers: _headers))) as Map<String, dynamic>;

  /// Link Gmail: the address is saved in the profile, the app password as a write-only secret. [test] checks the
  /// login against smtp.gmail.com first.
  Future<Map<String, dynamic>> linkGmail(String email, String appPassword, {bool test = true}) async =>
      (await _send(() => _http.put(_u('/api/v1/accounts/gmail'),
          headers: {..._headers, 'Content-Type': 'application/json'},
          body: jsonEncode({'email': email, 'app_password': appPassword, 'test': test})))) as Map<String, dynamic>;

  Future<Map<String, dynamic>> secrets() async =>
      (await _send(() => _http.get(_u('/api/v1/secrets'), headers: _headers))) as Map<String, dynamic>;

  Future<void> setSecret(String name, String value) async => _send(() => _http.put(_u('/api/v1/secrets/$name'),
      headers: {..._headers, 'Content-Type': 'application/json'}, body: jsonEncode({'value': value})));
}

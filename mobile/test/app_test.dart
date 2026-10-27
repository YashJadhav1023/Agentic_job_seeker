import 'dart:convert';

import 'package:agentic_job_seeker/api/client.dart';
import 'package:agentic_job_seeker/models/tool_spec.dart';
import 'package:agentic_job_seeker/ui/widgets/schema_form.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

void main() {
  group('ApiClient', () {
    test('sends bearer token and parses tool result', () async {
      late http.Request seen;
      final c = ApiClient(
        baseUrl: 'https://jobs.example.com/',
        token: 'x' * 40,
        httpClient: MockClient((req) async {
          seen = req;
          return http.Response(jsonEncode({'tool': 'apply_jobs', 'is_error': false, 'result': {'run_id': 'apply-1'}}), 200);
        }),
      );
      final r = await c.call('apply_jobs', {'platform': 'naukri', 'dry_run': true});
      expect(seen.url.toString(), 'https://jobs.example.com/api/v1/tools/apply_jobs');
      expect(seen.headers['Authorization'], 'Bearer ${'x' * 40}');
      expect(jsonDecode(seen.body), {'platform': 'naukri', 'dry_run': true});
      expect(r.runId, 'apply-1');
      expect(r.isError, isFalse);
    });

    test('maps HTTP errors to ApiException with detail', () async {
      final c = ApiClient(
        baseUrl: 'https://h',
        token: 't',
        httpClient: MockClient((_) async => http.Response(jsonEncode({'detail': 'invalid or missing bearer token'}), 401)),
      );
      expect(
        () => c.tools(),
        throwsA(isA<ApiException>().having((e) => e.unauthorized, 'unauthorized', isTrue)),
      );
    });
  });

  test('ToolSpec parses schema, required and annotations', () {
    final t = ToolSpec.fromJson({
      'name': 'send_email',
      'description': 'Send one email',
      'inputSchema': {
        'type': 'object',
        'properties': {'to': {'type': 'string'}, 'dry_run': {'type': 'boolean', 'default': true}},
        'required': ['to'],
      },
      'annotations': {'readOnlyHint': false, 'destructiveHint': true},
    });
    expect(t.title, 'Send email');
    expect(t.required, ['to']);
    expect(t.destructive, isTrue);
    expect(t.category, 'Outreach');
  });

  testWidgets('SchemaForm builds typed args and validates required fields', (tester) async {
    Map<String, dynamic> args = {};
    final key = GlobalKey<SchemaFormState>();
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          child: SchemaForm(
            key: key,
            properties: {
              'email': {'type': 'string'},
              'limit': {'type': 'integer', 'minimum': 1, 'maximum': 30},
              'dry_run': {'type': 'boolean', 'default': true},
              'sites': {'type': 'array', 'items': {'type': 'string', 'enum': ['linkedin', 'indeed']}},
              'names': {'type': 'array', 'items': {'type': 'string'}},
            },
            required: const ['email'],
            onChanged: (v) => args = v,
          ),
        ),
      ),
    ));
    await tester.pump();
    expect(key.currentState!.validate(), isFalse); // email missing

    await tester.enterText(find.widgetWithText(TextFormField, 'email *'), 'hr@acme.in');
    await tester.enterText(find.widgetWithText(TextFormField, 'limit'), '99');
    await tester.pump();
    expect(key.currentState!.validate(), isFalse); // limit above maximum

    await tester.enterText(find.widgetWithText(TextFormField, 'limit'), '5');
    await tester.enterText(find.widgetWithText(TextFormField, 'names'), 'A B, C D');
    await tester.tap(find.text('indeed'));
    await tester.pump();
    expect(key.currentState!.validate(), isTrue);
    expect(args, {
      'email': 'hr@acme.in',
      'limit': 5,
      'dry_run': true,
      'sites': ['indeed'],
      'names': ['A B', 'C D'],
    });
  });
}

import 'dart:convert';

import 'package:agentic_job_seeker/api/client.dart';
import 'package:agentic_job_seeker/state/providers.dart';
import 'package:agentic_job_seeker/ui/widgets/attention_banner.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

Future<List<Map<String, dynamic>>> _pump(WidgetTester tester, Map<String, dynamic> row) async {
  final sent = <Map<String, dynamic>>[];
  final client = ApiClient(
    baseUrl: 'https://jobs.example.com',
    token: 'x' * 40,
    httpClient: MockClient((req) async {
      sent.add({'path': req.url.path, ...jsonDecode(req.body) as Map<String, dynamic>});
      return http.Response(jsonEncode({'tool': 'attention_resolve', 'is_error': false, 'result': {'closed': true}}), 200);
    }),
  );
  await tester.pumpWidget(ProviderScope(
    overrides: [
      clientProvider.overrideWithValue(client),
      attentionProvider.overrideWith((ref) async => [row]),
    ],
    child: const MaterialApp(home: Scaffold(body: SingleChildScrollView(child: AttentionBanner()))),
  ));
  await tester.pump();
  return sent;
}

void main() {
  testWidgets('otp request: type the code in the app and send it to the bot', (tester) async {
    final sent = await _pump(tester, {
      'id': 7, 'kind': 'otp', 'site': 'acme.wd5.myworkdayjobs.com', 'job': 'SRE @ Acme', 'needs_value': true,
      'reason': 'Enter the code acme.wd5.myworkdayjobs.com sent to your email or phone', 'open_url': '', 'minutes_left': 14,
    });
    expect(find.text('Code needed: acme.wd5.myworkdayjobs.com'), findsOneWidget);
    expect(find.text('Sign In / Register'), findsNothing);
    await tester.enterText(find.byType(TextField), ' 482913 ');
    await tester.tap(find.text('Send'));
    await tester.pump();
    expect(sent, [
      {'path': '/api/v1/tools/attention_resolve', 'id': 7, 'value': '482913'},
    ]);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('question request shows the question and sends the answer', (tester) async {
    final sent = await _pump(tester, {
      'id': 9, 'kind': 'question', 'site': 'Acme', 'job': 'SRE @ Acme', 'needs_value': true,
      'reason': 'Willing to work night shifts? Options: Yes / No', 'open_url': '', 'minutes_left': 10,
    });
    expect(find.text('Question from Acme'), findsOneWidget);
    expect(find.text('Willing to work night shifts? Options: Yes / No'), findsOneWidget);
    await tester.tap(find.text('Send'));
    await tester.pump();
    expect(sent, isEmpty, reason: 'an empty answer is not sent');
    await tester.enterText(find.byType(TextField), 'Yes');
    await tester.tap(find.text('Send'));
    await tester.pump();
    expect(sent.single['value'], 'Yes');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('login request keeps the browser button and has no answer box', (tester) async {
    await _pump(tester, {
      'id': 3, 'kind': 'login', 'site': 'Workday', 'job': '', 'needs_value': false, 'reason': 'needs an account',
      'open_url': 'https://jobs.example.com/api/v1/attention/open?id=3&k=z', 'minutes_left': 15,
    });
    expect(find.text('Sign In / Register'), findsOneWidget);
    expect(find.byType(TextField), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });
}

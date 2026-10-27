import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../state/providers.dart';
import '../widgets/common.dart';

/// HR / recruiter contacts collected by discovery tools, with verify / suppress / email shortcuts.
class ContactsScreen extends ConsumerWidget {
  const ContactsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final contacts = ref.watch(contactsProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('HR contacts'), actions: [
        IconButton(
          tooltip: 'Add contact from a hiring post',
          icon: const Icon(Icons.person_add_alt),
          onPressed: () => context.push('/tools/add_hr_contact'),
        ),
        IconButton(icon: const Icon(Icons.refresh), onPressed: () => ref.invalidate(contactsProvider)),
      ]),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () => context.push('/tools/send_hr_emails', extra: {'dry_run': true}),
        icon: const Icon(Icons.forward_to_inbox),
        label: const Text('Email best contacts'),
      ),
      body: AsyncBody<List<Map<String, dynamic>>>(
        value: contacts,
        onRetry: () => ref.invalidate(contactsProvider),
        data: (list) => list.isEmpty
            ? Center(
                child: Column(mainAxisSize: MainAxisSize.min, children: [
                  const Text('No contacts yet'),
                  const SizedBox(height: 12),
                  FilledButton.tonal(
                    onPressed: () => context.push('/tools/find_hr_emails'),
                    child: const Text('Discover HR emails in a city'),
                  ),
                ]),
              )
            : RefreshIndicator(
                onRefresh: () => ref.refresh(contactsProvider.future),
                child: ListView.separated(
                  padding: const EdgeInsets.only(bottom: 88),
                  itemCount: list.length,
                  separatorBuilder: (_, _) => const Divider(height: 1),
                  itemBuilder: (c, i) {
                    final ct = list[i];
                    final email = '${ct['email']}';
                    return ListTile(
                      title: Text(email),
                      subtitle: Text([ct['company'], ct['role'], ct['city']].where((e) => '${e ?? ''}'.isNotEmpty).join(' · '),
                          maxLines: 2, overflow: TextOverflow.ellipsis),
                      leading: StatusChip('${ct['confidence']}'),
                      trailing: PopupMenuButton<String>(
                        onSelected: (v) {
                          switch (v) {
                            case 'copy':
                              Clipboard.setData(ClipboardData(text: email));
                            case 'verify':
                              context.push('/tools/verify_email', extra: {'email': email});
                            case 'email':
                              context.push('/tools/send_email', extra: {'to': email, 'dry_run': true});
                            case 'suppress':
                              context.push('/tools/suppress_contact', extra: {'email_or_domain': email});
                          }
                        },
                        itemBuilder: (_) => const [
                          PopupMenuItem(value: 'copy', child: Text('Copy address')),
                          PopupMenuItem(value: 'verify', child: Text('Verify deliverability')),
                          PopupMenuItem(value: 'email', child: Text('Write email')),
                          PopupMenuItem(value: 'suppress', child: Text('Never email again')),
                        ],
                      ),
                    );
                  },
                ),
              ),
      ),
    );
  }
}

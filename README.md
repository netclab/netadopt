# netadopt

Reads a network-automation repository and tells you what is in it.

```
netadopt avd ./repo --playbook build.yml
```

Reports the Ansible it would use, the inventory Ansible resolves, and the plays as
written. It reads *repositories*, never devices.

Bring your own Ansible, or:

```
uvx "netadopt[ansible]" avd ./repo
```

Early, and incomplete on purpose: today it only reports.

Apache-2.0

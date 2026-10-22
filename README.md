# netadopt

Reads a network-automation repository, says what is in it, and writes the
repository's model as Kubernetes resources. It reads *repositories*, never devices.

Arista AVD first: netadopt uses AVD's own data model - no new schema, no translation.
The resources carry the repository's inventory, play and vars exactly as written.

Screenshots, and a quick start from AVD's own example to a containerlab twin run from
Kubernetes: [netclab.dev/avd](https://netclab.dev/avd/).

## Installing

With [uv](https://docs.astral.sh/uv/), to run it once without installing:

```
uvx "netadopt[avd]" avd report ./repo --playbook build.yml
```

or to install it:

```
uv tool install "netadopt[avd]"
```

The `avd` extra installs everything netadopt needs to run AVD: ansible-core, pyavd,
and the two libraries AVD requires with them, distlib and netaddr. pyavd is pinned to
the AVD version shown by `netadopt --version`. netadopt uses only this Ansible; one
already installed elsewhere on the machine is not used.

## Four commands

| Command | Answers | Renders with AVD |
|---|---|---|
| `netadopt avd report` | What is in the repository, and what of it cannot be carried? | no - seconds |
| `netadopt avd emit` | The YAML for Kubernetes: one `Fabric`, a `FabricInput` per group or host | no - seconds |
| `netadopt avd compare` | Does emit's YAML carry the repository? Every host, compared | twice |
| `netadopt avd lab` | A lab of the fabric's switches, cabled as AVD cables them: netclab-chart or containerlab | once |

Each takes the repository's directory and the playbook it is built with. `report` also
runs without a playbook, and names the files that read as one.

## Reading an AVD repository

The examples here are AVD's own `single-dc-l3ls`, because anyone can clone it; the
same commands run on any AVD repository, within the
[Limits](https://github.com/netclab/netadopt#limits):

```
$ netadopt avd report single-dc-l3ls --playbook build.yml
single-dc-l3ls

Ansible     ansible-core 2.21.4    ~/.local/share/uv/tools/netadopt/bin/ansible-playbook
Config      ansible.cfg
Inventory   named by ansible.cfg   8 hosts, 8 groups
Variables   8 files                group_vars 8, host_vars 0
Playbook    build.yml              1 play
Renders     not measured           netadopt avd compare single-dc-l3ls --playbook build.yml

#   Play                                     Hosts    Roles
─────────────────────────────────────────────────────────────────────────────────────
0   Build Configurations and Documentation   FABRIC   eos_designs, eos_cli_config_gen

Carried
  Fabric         single-dc-l3ls, from play 0
  FabricInputs   7

Warnings
  ansible_password   plain text in FabricInput single-dc-l3ls-fabric, carried as the repository has it
```

- **Carried** is what `emit` writes.
- **Not carried** is what it leaves behind, each with the reason. On AVD's
  `cv-pathfinder` example, the vault password:

  ```
  Not carried
    .vault   the vault password; create its Secret:
      kubectl create secret generic cv-pathfinder-vault --from-file=password=cv-pathfinder/.vault
  ```

- **Warnings** are carried, but worth knowing: a password in plain text, a vars file
  for a host the inventory does not have.
- **Renders** is not measured here. Rendering runs everything the vars hold and needs
  AVD's collections, so it belongs to `netadopt avd compare`, and the row is the
  command that measures it. A report says the model was read, never that it builds.

Exit codes: `0` when nothing in the repository stops it being carried, warnings or
not; `2` when a part of it is not carried or something did not read; `1` when netadopt
was installed without the `avd` extra, and then nothing else is reported. A `0` is not
the proof that emit's YAML carries the repository: `compare` is.

## Writing Kubernetes manifests

```
$ netadopt avd emit single-dc-l3ls --playbook build.yml > single-dc-l3ls.yaml
```

One `Fabric` - the play, the inventory and `ansible.cfg`, as written - and one
`FabricInput` per group or host that carries vars:

```yaml
apiVersion: avd.netclab.dev/v1alpha1
kind: FabricInput
metadata:
  name: single-dc-l3ls-dc1
  labels:
    avd.netclab.dev/fabric: single-dc-l3ls
spec:
  appliesTo:
    group: DC1
  beside: inventory
  design:
    mgmt_gateway: 172.16.1.1
    management_eapi:
      enabled: true
```

Group and host names stay the repository's; only `metadata.name` is spelled for
Kubernetes. The YAML goes to stdout and everything said about it to stderr.

Extra vars are given as to `ansible-playbook`, and the `Fabric` carries them as
`spec.extraVars`:

```
$ netadopt avd emit single-dc-l3ls --playbook build.yml -e @lab-vars.yml > single-dc-l3ls.yaml
```

Only the `@file` form is read for now. Given twice, the later file wins a key.

Apply them server-side:

```
kubectl apply --server-side -f single-dc-l3ls.yaml
```

A plain `kubectl apply` drops every `key: null` in a map, and AVD reads a null
differently from a missing key.

## Proving emit's YAML carries the repository

```
$ netadopt avd compare single-dc-l3ls --playbook build.yml
rendering play [0] of build.yml with arista.avd.eos_designs
rendering the repository rebuilt from emit's YAML
single-dc-l3ls

Ansible      ansible-core 2.21.4   ~/.local/share/uv/tools/netadopt/bin/ansible-playbook
Repository   rendered              8 hosts
Rebuilt      rendered              8 hosts
Compared     identical             8 of 8 hosts identical
```

`compare` renders the fabric twice with AVD's own `eos_designs`: from the repository,
and from the repository rebuilt from the YAML `emit` writes - the one a cluster
renders. Then it compares every host's structured configuration, whole. Identical on
every host says that YAML carries the repository.

Anything else is named. A host that differs is listed with the keys of its structured
configuration that differ; a host only one render has, with the side it is on; a
render that fails, with what Ansible said. A file the vars read that `emit` does not
carry fails the rebuilt render here, before a cluster ever sees it.

Both renders run as `lab`'s does, on copies, with the collections netadopt pins. The
vault password reaches the rebuilt repository from the file `ansible.cfg` names, as the
Secret does in a cluster. Extra vars are not given to either render.

Exit codes: `0` when every host is identical; `2` when a host differs, a render fails,
or emit's YAML cannot be written; `1` without the `avd` extra.

## Building a lab from the fabric

`lab` writes a lab topology of the same fabric: the cabling, as nodes and networks.

```
$ netadopt avd lab single-dc-l3ls --playbook build.yml --ceos-image ceos:4.36.1F > values.yaml
rendering play [0] of build.yml with arista.avd.eos_designs
rendered 8 hosts
10 nodes (8 ceos, 2 linux), 22 networks
```

The values are [netclab-chart](https://github.com/netclab/netclab-chart)'s:

```yaml
topology:
  networks:
  - name: n1
  - name: n2
  # ...
  nodes:
  - name: dc1-leaf1-server1
    type: linux
    interfaces:
    - name: ilo
      network: n1
    - name: pci1
      network: n2
    - name: pci2
      network: n3
  - name: dc1-leaf1a
    type: ceos
    image: ceos:4.36.1F
    interfaces:
    - name: eth1
      network: n4
    # ...
```

```
helm install lab netclab/netclab --values values.yaml
```

The nodes are cabled and carry none of the fabric's configuration. The cabling is
AVD's, so netadopt renders the repository with AVD to get it - on a copy, never in the
repository, and with the collections it pins, fetched into its own cache the first time
and never again.

The lab is the repository's twin as it is: once a run reaches `rendered N hosts`, the
repository builds on the AVD version shown by `netadopt --version`. A run that stops
instead prints what AVD refused, host by host. Whether emit's YAML carries it is
`compare`'s question, not `lab`'s.

A peer outside the fabric - a server, a firewall - becomes the node `--connected`
says: `linux` by default, or `ceos`, or `none` to leave it out. `--ceos-image` is
required: cEOS cannot be pulled, so import the image downloaded from Arista and name it.
`--ceos-memory` and `--ceos-cpu` are written only when given, so the chart's own
defaults hold otherwise.

Node names are the repository's hostnames, lowercased for Kubernetes. A host no name
can be spelled from is left out with its cables and said on stderr; two hosts that
would share a name stop the lab rather than one of them being guessed at.

With `--for containerlab`, a topology for [containerlab](https://containerlab.dev)
instead, cabled alike:

```
$ netadopt avd lab single-dc-l3ls --playbook build.yml --for containerlab --ceos-image ceos:4.36.1F > single-dc-l3ls.clab.yml
$ sudo containerlab deploy -t single-dc-l3ls.clab.yml
```

```yaml
name: single-dc-l3ls
mgmt:
  network: single-dc-l3ls
  ipv4-subnet: 172.20.20.0/24
topology:
  kinds:
    arista_ceos:
      image: ceos:4.36.1F
      startup-config: |
        !
        username arista privilege 15 secret 0 arista
        # ...
    linux:
      image: bash:5.3.20
  nodes:
    dc1-leaf1-server1:
      kind: linux
      mgmt-ipv4: 172.20.20.2
    dc1-leaf1a:
      kind: arista_ceos
      mgmt-ipv4: 172.20.20.3
    # ...
  links:
  - endpoints: ['dc1-leaf1-server1:ilo', 'dc1-leaf1c:eth5']
  # ...
```

`--ceos-memory`, `--ceos-cpu` and `--namespace` are netclab's, and refused here. Every
cEOS node starts from netclab-chart's own startup-config - the user `arista`, eAPI over
HTTPS, and a control plane that lets the fabric's protocols in - so the two labs' nodes
start alike. As in netclab, EOS leaves `eth0` to the container, so a push replacing the
configuration keeps the node's management address. A `.clab.yml` is containerlab's own
name for a topology, and the one
[vscode-containerlab](https://marketplace.visualstudio.com/items?itemName=srl-labs.vscode-containerlab)
finds a lab by.

Every node has a fixed management address, in name order, in containerlab's default
subnet; `--mgmt-subnet` and `--mgmt-network` choose others. A subnet another Docker
network already holds stops `containerlab deploy`, naming that network: give a free
one. A network of more than two ends is containerlab's bridge inside a node of the
network's own name, so nothing has to exist on the host first. containerlab itself adds
the nodes to `/etc/hosts` and to the SSH configuration, and `containerlab destroy`
takes them out.

To push the fabric's configuration to the lab rather than to the real devices, `lab`
also writes the extra vars that point each host at its node - a Service in netclab,
the node's address in containerlab:

```
$ netadopt avd lab single-dc-l3ls --playbook build.yml --ceos-image ceos:4.36.1F --namespace dc1 --extra-vars-out lab-vars.yml > values.yaml
$ helm install lab netclab/netclab --namespace dc1 --values values.yaml
$ netadopt avd emit single-dc-l3ls --playbook build.yml -e @lab-vars.yml > single-dc-l3ls.yaml
```

```yaml
lab_services:            # lab_addresses in containerlab: dc1-leaf1a: 172.20.20.3
  dc1-leaf1a: dc1-leaf1a.dc1.svc
  # ...
ansible_host: '{{ lab_services[inventory_hostname] | mandatory(inventory_hostname ~ '' has no node in the lab'') }}'
management_eapi:
  enabled: true
  vrfs:
  - name: default
    enabled: true
ansible_user: arista
ansible_password: arista
lab_local_user:
  name: arista
  # ...
lab_radius: {}
aaa_settings: '{{ {''local_users'': [lab_local_user]} | combine({''radius'': lab_radius[inventory_hostname]} if inventory_hostname in lab_radius else {}) }}'
```

In netclab the namespace is the one the chart is installed into, and `--extra-vars-out`
refuses to run without it. A host with no node in the lab stops with its name, never
reaching an address that is not its own. eAPI moves to the default VRF, because a cEOS
node has no management interface for the design's own.

The lab's AAA is the lab's own, whatever the design's: the push logs in as `arista`,
and `aaa_settings` becomes that one local user, so that no TACACS+ or RADIUS server the
lab cannot reach stands between a push and a switch, and the user is still there after
a push replaces the configuration. A host whose design has RADIUS keeps it, because
802.1X does not render without a server.

Exit codes are `report`'s: `0`, or `2` when there is no lab, or `1` without the `avd`
extra.

## Before running it on someone else's repository

`report` asks Ansible about the inventory, in the repository. Ansible then runs
whatever the repository gives it: an executable inventory, and the plugin directories
named in its `ansible.cfg`. `report` names both under Warnings - after running them.

`emit` runs no Ansible; it only reads files. Neither renders Jinja.

`lab` and `compare` do render it, with AVD, so everything in the vars runs - a
`lookup('pipe', ...)` included. They render on copies of the repository, never in it,
and they are the commands that reach the network themselves: on the first run of
either, to fetch the collections netadopt pins.

None of them connects to a device.

## Tested on AVD's own repositories

Each release is tested on the examples and test scenarios that ship with AVD, at the
version shown by `netadopt --version`, among them:

- `eos_designs-twodc-5stage-clos`: three plays, a node-ID pool, Jinja templates of its own
- `cv-pathfinder`: a vaulted password
- the unit-test scenarios, with hundreds of hosts

Every one that can be carried is emitted, rebuilt from emit's YAML, and resolved by
Ansible on both sides, and every host must end up with the same variables. Every one
that renders is rendered from emit's YAML too, and every host's structured
configuration must equal the repository's own, as `compare` checks it. Every one that
renders is also built into a lab, where each cable AVD wrote has to reach the topology
or be named as left out.

## Limits

- AVD is the version `netadopt --version` names. A vars file of the play's hosts
  holding a key that AVD removed is named under Not carried, and `report` exits 2.
- A file the vars read with `lookup('file', ...)` is not carried: the lookup is named
  under Not carried, and `report` and `emit` exit 2.
- The inventory must be a YAML file. An executable or dynamic inventory is not
  carried.
- A code directory named in `ansible.cfg`, such as `vars_plugins = plugins/vars`, is not
  carried.
- `collections_path` in `ansible.cfg` is not used: `compare`, `lab` and the cluster
  take AVD's collections from netadopt, not from the repository.
- One play per `Fabric`, and one play per lab. Another play of the same playbook is
  another run: `report --play N --name NAME`, `emit` the same, `compare --play N`,
  `lab --play N`. Each names the plays it did not carry.
- `compare` gives neither render extra vars: a repository built with `-e` is compared
  without them.
- The play is rendered as one role, `eos_designs`. A task before it other than an
  `arista.avd` role, `vars_files`, a template in `hosts`, or a `hosts` naming no group
  or host of the inventory is named under Not carried, and `report` exits 2: simplify
  the play, or carry it by hand.
- `lab` cables `EthernetN` only. A subinterface rides on its parent's cable, and a
  breakout such as `Ethernet1/4` is left out and named.
- netclab-chart names a veth `<release>-<network>-<hash>` within the 15 bytes Linux
  allows, and refuses to render when there is no room left, so keep the Helm release
  name short: nine characters fits every lab up to `n99`.

## Where this is going

- A second ecosystem after AVD, under a subcommand of its own, with its model kept as
  its vendor writes it.

## Developing

Running the checks, the two test tiers and the release:
[DEVELOPMENT.md](https://github.com/netclab/netadopt/blob/main/DEVELOPMENT.md).

## License

[Apache-2.0](https://github.com/netclab/netadopt/blob/main/LICENSE)

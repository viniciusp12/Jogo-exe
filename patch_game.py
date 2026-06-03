#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==============================================================================
 patch_game.py  -  Patcher do "game_01.exe"
==============================================================================

 Aplica TODAS as modificacoes que fizemos, a partir do executavel ORIGINAL:

   1. VIDA / MANA / ENERGIA infinitas  (hook na funcao printTela via code cave)
   2. Pocoes iniciais 9 -> 99
   3. One-hit-kill   (inimigo sempre morre apos o ataque)
   4. Credito "FIAP - Mauricio Neto..." -> "OLIVETTI AQUI"
   5. Titulo grande "MAURICIO GAMES" -> "OLIVETTI / ESTEVE / AQUI"

 Uso:
     pip install capstone        (opcional, so para verificacao no final)
     python patch_game.py game_01.exe game_FINAL.exe

 IMPORTANTE: todos os offsets/enderecos abaixo sao ESPECIFICOS deste binario.
 Eles foram descobertos com `objdump -t` (simbolos) e `objdump -d` (disassembly).
 Se o seu amigo recompilar o jogo, os enderecos mudam e o script precisa ser
 reajustado.
==============================================================================
"""

import sys
import struct
import shutil


# ----------------------------------------------------------------------------
# CONVERSAO DE ENDERECOS
# ----------------------------------------------------------------------------
# O disassembly usa enderecos de MEMORIA (VMA). Para editar o ARQUIVO precisamos
# do offset dentro dele. A secao .text comeca em VMA 0x401000 e no arquivo no
# offset 0x600. Logo:  file_offset = VMA - 0x401000 + 0x600
def vma_to_off(vma):
    return vma - 0x401000 + 0x600


# Enderecos do struct "voce" (o personagem) e seus campos, achados com objdump:
#   voce       @ VMA 0x42bb60
#   voce+0x0c  = VIDA
#   voce+0x10  = MANA
#   voce+0x14  = ENERGIA
VOCE      = 0x42bb60
VIDA      = VOCE + 0x0c   # 0x42bb6c
MANA      = VOCE + 0x10   # 0x42bb70
ENERGIA   = VOCE + 0x14   # 0x42bb74

PRINTF    = 0x424960      # endereco da funcao printf (da libc)
PRINTTELA = 0x4045f5      # funcao que redesenha a tela inteira

# Code cave: 320 bytes de espaco executavel vazio no fim da secao .text
# (entre o fim do codigo e o inicio da secao .data). Mapeia para VMA 0x424ac0.
CAVE_VMA  = 0x424ac0

VALOR_MAX = 0x270f        # 9999 - valor que vamos forcar em vida/mana/energia


# ----------------------------------------------------------------------------
# HELPERS PARA MONTAR INSTRUCOES x86-64
# ----------------------------------------------------------------------------
def mov_mem_imm(target_vma, imm, cur_addr):
    """
    Monta:  mov dword ptr [rip+disp32], imm32   (10 bytes)
    Opcode: C7 05 <disp32> <imm32>
    'disp32' eh relativo ao FIM da instrucao (cur_addr + 10), por isso o RIP.
    """
    next_addr = cur_addr + 10
    disp = (target_vma - next_addr) & 0xFFFFFFFF
    return bytes([0xC7, 0x05]) + struct.pack('<I', disp) + struct.pack('<I', imm)


def jmp_rel32(target_vma, cur_addr):
    """ Monta:  jmp rel32  (5 bytes)  ->  E9 <disp32> """
    disp = (target_vma - (cur_addr + 5)) & 0xFFFFFFFF
    return bytes([0xE9]) + struct.pack('<I', disp)


def lea_rip(modrm, target_vma, cur_addr):
    """
    Monta:  lea reg, [rip+disp32]  (7 bytes)  ->  48 8D <modrm> <disp32>
    modrm 0x0D = rcx, 0x15 = rdx
    """
    disp = (target_vma - (cur_addr + 7)) & 0xFFFFFFFF
    return bytes([0x48, 0x8D, modrm]) + struct.pack('<i',
        struct.unpack('<i', struct.pack('<I', disp))[0])


def call_rel32(target_vma, cur_addr):
    """ Monta:  call rel32  (5 bytes)  ->  E8 <disp32> """
    disp = (target_vma - (cur_addr + 5)) & 0xFFFFFFFF
    return bytes([0xE8]) + struct.pack('<I', disp)


# ----------------------------------------------------------------------------
# FONTE DE BLOCO (5 linhas de altura) PARA O TITULO NOVO
# ----------------------------------------------------------------------------
FONT = {
    'O': ["####", "#  #", "#  #", "#  #", "####"],
    'L': ["#   ", "#   ", "#   ", "#   ", "####"],
    'I': ["###", " # ", " # ", " # ", "###"],
    'V': ["#  #", "#  #", "#  #", " ## ", " ## "],
    'E': ["####", "#   ", "### ", "#   ", "####"],
    'T': ["###", " # ", " # ", " # ", " # "],
    'A': ["####", "#  #", "####", "#  #", "#  #"],
    'Q': ["####", "#  #", "#  #", "####", "   #"],
    'U': ["#  #", "#  #", "#  #", "#  #", "####"],
    'S': ["####", "#   ", "####", "   #", "####"],
    ' ': ["  ", "  ", "  ", "  ", "  "],
}


def render_word(word):
    """Retorna as 5 linhas (strings com '#') que desenham a palavra."""
    rows = [''] * 5
    for ch in word:
        g = FONT[ch]
        for i in range(5):
            rows[i] += g[i] + ' '
    return rows


# ----------------------------------------------------------------------------
# OS PATCHES
# ----------------------------------------------------------------------------
def patch_valores_iniciais(data):
    """1. Vida/Mana/Energia iniciais 10->99 e pocoes 9->99."""
    # As instrucoes de init sao 'mov dword ptr [rax+off], imm'. O byte do valor
    # imediato fica num offset fixo dentro de cada instrucao (descoberto no disasm).
    data[0xba3] = 0x63   # VIDA   10 -> 99
    data[0xbb1] = 0x63   # MANA   10 -> 99
    data[0xbbf] = 0x63   # ENERGIA 10 -> 99
    data[0xfa1] = 0x63   # pocao_mana    9 -> 99
    data[0xfab] = 0x63   # pocao_energia 9 -> 99
    data[0xfb5] = 0x63   # pocao_vida    9 -> 99
    print("[1] Valores iniciais e pocoes -> 99")


def patch_stats_infinitos(data):
    """
    2. Vida/Mana/Energia infinitas.
    Hook no inicio de printTela: desvia para o code cave, que forca os 3 stats
    para 9999 e depois re-executa as instrucoes originais e volta.
    Como printTela roda a cada frame, os stats nunca esvaziam.
    """
    # ---- monta o codigo do cave ----
    cave = bytearray()
    cur = CAVE_VMA
    for target in (VIDA, MANA, ENERGIA):
        cave += mov_mem_imm(target, VALOR_MAX, cur)
        cur += 10
    # re-executa as 2 instrucoes originais que o hook vai sobrescrever:
    #   push rbp                = 55
    #   sub rsp, 0x290          = 48 81 EC 90 02 00 00
    cave += bytes([0x55]); cur += 1
    cave += bytes([0x48, 0x81, 0xEC, 0x90, 0x02, 0x00, 0x00]); cur += 7
    # volta para printTela+8 (logo depois das instrucoes deslocadas)
    cave += jmp_rel32(PRINTTELA + 8, cur)

    data[vma_to_off(CAVE_VMA):vma_to_off(CAVE_VMA) + len(cave)] = cave

    # ---- escreve o hook (jmp para o cave) no inicio do printTela ----
    hook = jmp_rel32(CAVE_VMA, PRINTTELA) + bytes([0x90, 0x90, 0x90])  # 5+3 = 8 bytes
    data[vma_to_off(PRINTTELA):vma_to_off(PRINTTELA) + len(hook)] = hook
    print("[2] Vida/Mana/Energia infinitas (hook no printTela)")


def patch_one_hit_kill(data):
    """
    3. One-hit-kill.
    Na funcao Batalha existe a checagem:
        mov eax, [inimigo_vida]
        test eax, eax
        jg 0x4043ce        ; pula = inimigo VIVO (luta continua)
    Trocando o 'jg' (7F 19) por dois NOP (90 90), o codigo NUNCA pula -> sempre
    cai no caminho de "inimigo morto" depois do ataque.
    """
    off = vma_to_off(0x4043b3)
    assert data[off] == 0x7F and data[off + 1] == 0x19, "padrao do jg inesperado!"
    data[off] = 0x90
    data[off + 1] = 0x90
    print("[3] One-hit-kill (jg -> nop nop)")


def patch_credito(data):
    """
    4. Credito "FIAP - Mauricio Neto..." -> "OLIVETTI AQUI".
    O credito eh impresso caractere por caractere: cada letra eh um
    'mov ecx, <ascii>' (5 bytes) seguido de 'call putchar'. Basta sobrescrever
    o byte do imediato (em VMA+1) de cada slot. Comeca em 0x4064c0, 10 bytes/slot.
    """
    start = 0x4064c0
    slots = []
    vma = start
    while True:
        o = vma_to_off(vma)
        if data[o] != 0xB9 or data[o + 5] != 0xE8:  # mov ecx,imm + call?
            break
        slots.append(vma)
        imm = data[o + 1]
        vma += 10
        if imm == 0x29:   # ')' = ultimo char do credito original
            break

    novo = "OLIVETTI AQUI".ljust(len(slots), ' ')  # preenche o resto com espaco
    for v, ch in zip(slots, novo):
        data[vma_to_off(v) + 1] = ord(ch)
    print(f"[4] Credito -> 'OLIVETTI AQUI' ({len(slots)} slots)")


def patch_titulo(data):
    """
    5. Titulo grande "MAURICIO GAMES" -> "OLIVETTI / ESTEVE / AQUI".
    A arte original eh desenhada por centenas de instrucoes entre 0x40498b
    (primeira linha de conteudo da caixa) e 0x4064c0 (inicio do credito).
    Em vez de editar tudo, sobrescrevemos esse trecho por uma rotina propria:
        lea rcx, ["%s"]
        lea rdx, [blob]      ; blob = todas as linhas da nova arte, ja com bordas
        call printf
        jmp 0x4064c0         ; volta para o credito (caixa intacta)
    """
    # ---- monta a arte (25 linhas de 78 colunas, mesma altura da original) ----
    art = []
    for word in ["OLIVETTI", "ESTEVE", "AQUI"]:
        for line in render_word(word):
            line = line.rstrip()
            pad = (78 - len(line)) // 2
            art.append(' ' * pad + line + ' ' * (78 - pad - len(line)))
        art.append(' ' * 78)           # linha em branco entre palavras
    content = [' ' * 78] * 3 + art + [' ' * 78] * 4   # 3 + 18 + 4 = 25 linhas
    assert len(content) == 25

    # cada linha vira:  0xBA (borda esq) + 78 chars + 0xBA (borda dir) + 0x0A (\n)
    # '#' -> 0xDB (bloco solido █),  espaco -> 0x20
    blob = b''
    for row in content:
        rb = bytes(0xDB if c == '#' else 0x20 for c in row)
        blob += bytes([0xBA]) + rb + bytes([0xBA, 0x0A])
    blob += b'\x00'                     # terminador da string C

    # ---- monta a rotina (24 bytes) ----
    rotina_vma = 0x40498b
    CODE_LEN = 24
    fmt_vma  = rotina_vma + CODE_LEN    # logo apos a rotina vem "%s\0"
    blob_vma = fmt_vma + 3              # e depois o blob

    code = bytearray()
    a = rotina_vma
    code += lea_rip(0x0D, fmt_vma, a);  a += 7   # lea rcx, [fmt]
    code += lea_rip(0x15, blob_vma, a); a += 7   # lea rdx, [blob]
    code += call_rel32(PRINTF, a);      a += 5   # call printf
    code += jmp_rel32(0x4064c0, a);     a += 5   # jmp credito
    assert len(code) == CODE_LEN

    payload = code + b'%s\x00' + blob
    espaco = 0x4064c0 - rotina_vma
    assert len(payload) <= espaco, "payload nao cabe no trecho!"

    o = vma_to_off(rotina_vma)
    data[o:o + len(payload)] = payload
    # preenche o resto do trecho com NOP, para nao deixar lixo executavel
    for x in range(vma_to_off(rotina_vma + len(payload)), vma_to_off(0x4064c0)):
        data[x] = 0x90
    print("[5] Titulo grande -> 'OLIVETTI / ESTEVE / AQUI'")


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------
def main():
    if len(sys.argv) != 3:
        print("uso: python patch_game.py <original.exe> <saida.exe>")
        sys.exit(1)

    src, dst = sys.argv[1], sys.argv[2]
    shutil.copy2(src, dst)

    with open(dst, 'r+b') as f:
        data = bytearray(f.read())

    # valida que eh um PE valido antes de mexer
    assert data[:2] == b'MZ', "nao parece um executavel Windows (.exe)"

    patch_valores_iniciais(data)
    patch_stats_infinitos(data)
    patch_one_hit_kill(data)
    patch_credito(data)
    patch_titulo(data)

    with open(dst, 'wb') as f:
        f.write(data)

    # ---- verificacao opcional com capstone ----
    try:
        from capstone import Cs, CS_ARCH_X86, CS_MODE_64
        md = Cs(CS_ARCH_X86, CS_MODE_64)
        print("\n--- verificacao do hook no printTela ---")
        for ins in md.disasm(bytes(data[vma_to_off(PRINTTELA):vma_to_off(PRINTTELA) + 5]), PRINTTELA):
            print(f"  0x{ins.address:x}: {ins.mnemonic} {ins.op_str}")
        print("--- verificacao do code cave ---")
        for ins in md.disasm(bytes(data[vma_to_off(CAVE_VMA):vma_to_off(CAVE_VMA) + 43]), CAVE_VMA):
            print(f"  0x{ins.address:x}: {ins.mnemonic} {ins.op_str}")
    except ImportError:
        print("\n(capstone nao instalado - pulei a verificacao. Instale com: pip install capstone)")

    print(f"\nPronto! Arquivo salvo em: {dst}")


if __name__ == "__main__":
    main()

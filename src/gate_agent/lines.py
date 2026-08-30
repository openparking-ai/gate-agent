"""Every sentence anybody hears, and the audio file that says it.

**A line is a VALUE in this repository, per language, tested.** Nothing here is
composed at runtime, nothing is interpolated, and there is no template: the file
that plays is the file whose name derives from the line's key and the language,
and the words in it are the words in `TEXT`. That is what makes "the driver
heard what the test asserts" checkable at all -- a synthesiser at runtime would
mean the product says whatever a model said that morning.

**The line set is DERIVED, never listed.** The driver's case lines come from
`AgentCase` and the operator's menu lines from `Authorisation`, so a case or an
authorisation cannot be added without every language noticing. A hard-coded list
here would be a list that cannot see what was added to what it is supposed to
cover.

**Two audiences, two rules.**

*The driver* has no keypad and no way to choose a language, so every line they
hear plays in EVERY declared driver language, in the declared order, one after
the other. Short sentences, because they are heard three times over at a
three-language site. A driver-chosen language waits for a channel that can carry
the choice and is not invented here.

*The operator* hears one language, the site's. They are staff, the site knows
which language they work in, and a menu played twice is a menu somebody keys
over.

**A line with no text in a declared language, or no audio file, is refused at
startup.** Not skipped, not substituted, not played in another language: a
driver who hears silence at a barrier has been told nothing, and a fallback
language is a driver told something in a language nobody chose for them.
"""

from __future__ import annotations

from .contract import AgentCase, Authorisation

#: The lines the DRIVER hears, in every declared driver language.
#: Derived: one per case, one per authorisation, plus the four the dialogue
#: itself needs.
DRIVER_LINES: tuple[str, ...] = (
    *(f"case.{case.value}" for case in AgentCase),
    *(f"authorisation.{value.value}" for value in Authorisation),
    "driver.human_unreachable",
    "driver.nothing_usable",
    "driver.hold_reprompt",
    "driver.undeclared_intercom",
)

#: The lines the OPERATOR hears, in the operator language only.
OPERATOR_LINES: tuple[str, ...] = (
    *(f"operator_case.{case.value}" for case in AgentCase),
    *(f"menu.{value.value}" for value in Authorisation),
    "operator.menu_repeat",
    "operator.menu_callback_number",
    "operator.cannot_open",
)

LINES: tuple[str, ...] = DRIVER_LINES + OPERATOR_LINES

#: The languages this repository ships text and audio for. A site may declare
#: only these; anything else is refused at startup with this list in the message,
#: because a language declared with no lines behind it is a silence with a
#: configuration key in front of it.
SHIPPED_LANGUAGES: tuple[str, ...] = ("en", "es")


#: Every line, in every shipped language. **The English is the source and the
#: Spanish is a translation of it**; both are checked into this repository, both
#: are tested, and the audio for each is generated from exactly this text -- see
#: `scripts/build_audio.py` and `audio/MANIFEST.json`, which records the
#: text each file was made from so that editing a sentence without regenerating
#: its audio goes red rather than shipping a file that says the old thing.
TEXT: dict[str, dict[str, str]] = {
    # --- what the driver is told the case is ------------------------------
    "case.malfunction_active": {
        "en": "There is a fault at this entrance. I am connecting you to a person.",
        "es": "Hay una avería en esta entrada. Le paso con una persona.",
    },
    # NEVER an instruction about the plate. The identification service is off,
    # and telling somebody to clean a plate nothing looked at is the failure
    # this whole module was reordered around.
    "case.identification_unavailable": {
        "en": "Our vehicle identification is not working right now. "
              "I am connecting you to a person.",
        "es": "Nuestra identificación de vehículos no funciona ahora mismo. "
              "Le paso con una persona.",
    },
    "case.plate_not_read": {
        "en": "We could not read your number plate. If something is covering it, please "
              "clear it. I am connecting you to a person.",
        "es": "No hemos podido leer su matrícula. Si algo la tapa, despéjela por favor. "
              "Le paso con una persona.",
    },
    "case.plate_unclear": {
        "en": "We could not read your number plate clearly. If something is covering it, "
              "please clear it. I am connecting you to a person.",
        "es": "No hemos podido leer su matrícula con claridad. Si algo la tapa, despéjela "
              "por favor. Le paso con una persona.",
    },
    "case.vehicle_not_recognised": {
        "en": "We do not recognise this vehicle. I am connecting you to a person.",
        "es": "No reconocemos este vehículo. Le paso con una persona.",
    },
    "case.rules_unavailable": {
        "en": "We cannot check this entrance's rules right now. "
              "I am connecting you to a person.",
        "es": "No podemos comprobar las normas de esta entrada ahora mismo. "
              "Le paso con una persona.",
    },
    "case.entry_refused": {
        "en": "This vehicle was not admitted. I am connecting you to a person.",
        "es": "Este vehículo no ha sido admitido. Le paso con una persona.",
    },
    "case.vehicle_not_detected": {
        "en": "The entrance did not detect a vehicle. I am connecting you to a person.",
        "es": "La entrada no ha detectado ningún vehículo. Le paso con una persona.",
    },
    "case.entry_not_confirmed": {
        "en": "We could not confirm that you drove through. "
              "I am connecting you to a person.",
        "es": "No hemos podido confirmar que haya pasado. Le paso con una persona.",
    },
    "case.unrecognised_reason": {
        "en": "We cannot tell what happened here. I am connecting you to a person.",
        "es": "No sabemos qué ha ocurrido aquí. Le paso con una persona.",
    },
    "case.lane_unavailable": {
        "en": "We cannot reach this entrance right now. I am connecting you to a person.",
        "es": "No podemos comunicar con esta entrada ahora mismo. Le paso con una persona.",
    },
    "case.standalone": {
        "en": "You are through to the parking intercom. I am connecting you to a person.",
        "es": "Ha llamado al interfono del aparcamiento. Le paso con una persona.",
    },
    "case.nothing_to_do": {
        "en": "This entrance has nothing outstanding for you. Goodbye.",
        "es": "Esta entrada no tiene nada pendiente para usted. Adiós.",
    },
    # --- what the driver is told the human decided ------------------------
    # `open_now` and `open_and_flag` do NOT say the barrier is opening. It is
    # not: nothing in this version can move one, and a driver who believes a
    # barrier is about to lift is worse off than one who was told to wait.
    "authorisation.open_now": {
        "en": "A person has authorised your entry. This system cannot open the barrier "
              "itself, so please wait for them.",
        "es": "Una persona ha autorizado su entrada. Este sistema no puede abrir la barrera "
              "por sí mismo; espere a que lo hagan.",
    },
    "authorisation.open_and_flag": {
        "en": "A person has authorised your entry and made a note of it. This system cannot "
              "open the barrier itself, so please wait for them.",
        "es": "Una persona ha autorizado su entrada y lo ha anotado. Este sistema no puede "
              "abrir la barrera por sí mismo; espere a que lo hagan.",
    },
    "authorisation.do_not_open": {
        "en": "A person has decided not to open the barrier.",
        "es": "Una persona ha decidido no abrir la barrera.",
    },
    "authorisation.hold": {
        "en": "Please hold. Somebody is dealing with this.",
        "es": "Espere, por favor. Alguien se está ocupando de esto.",
    },
    "authorisation.transfer": {
        "en": "I am putting you through to somebody else.",
        "es": "Le paso con otra persona.",
    },
    "authorisation.call_back": {
        "en": "A person will call you back.",
        "es": "Una persona le devolverá la llamada.",
    },
    # --- what the driver is told when nothing worked ----------------------
    "driver.human_unreachable": {
        "en": "Nobody answered. Please try this intercom again, or use the help number at "
              "this entrance.",
        "es": "No ha contestado nadie. Vuelva a llamar por este interfono o use el número de "
              "ayuda de esta entrada.",
    },
    "driver.nothing_usable": {
        "en": "I could not take an instruction. Please try this intercom again.",
        "es": "No he podido recoger una instrucción. Vuelva a llamar por este interfono.",
    },
    "driver.hold_reprompt": {
        "en": "Please keep holding. Somebody is dealing with this.",
        "es": "Siga esperando, por favor. Alguien se está ocupando de esto.",
    },
    "driver.undeclared_intercom": {
        "en": "This intercom is not configured. Goodbye.",
        "es": "Este interfono no está configurado. Adiós.",
    },
    # --- what the operator is told the case is ----------------------------
    # The LANE NAME is not here. It cannot be: a name is a site's value and no
    # sentence in this repository can say it. The site declares one audio file
    # per intercom saying where the call is from, and the agent plays that
    # first -- see `[intercoms.<uri>] name_audio`.
    "operator_case.malfunction_active": {
        "en": "A fault is active at this lane.", "es": "Hay una avería activa en este carril.",
    },
    "operator_case.identification_unavailable": {
        "en": "Vehicle identification is unavailable.",
        "es": "La identificación de vehículos no está disponible.",
    },
    "operator_case.plate_not_read": {
        "en": "The number plate was not read.", "es": "No se ha leído la matrícula.",
    },
    "operator_case.plate_unclear": {
        "en": "The number plate was unclear.", "es": "La matrícula no estaba clara.",
    },
    "operator_case.vehicle_not_recognised": {
        "en": "The vehicle was not recognised.", "es": "El vehículo no ha sido reconocido.",
    },
    "operator_case.rules_unavailable": {
        "en": "The entrance rules could not be checked.",
        "es": "No se han podido comprobar las normas de la entrada.",
    },
    "operator_case.entry_refused": {
        "en": "Entry was refused.", "es": "Se ha denegado la entrada.",
    },
    "operator_case.vehicle_not_detected": {
        "en": "The lane detected no vehicle.",
        "es": "El carril no ha detectado ningún vehículo.",
    },
    "operator_case.entry_not_confirmed": {
        "en": "The entry was not confirmed.", "es": "No se ha confirmado la entrada.",
    },
    "operator_case.unrecognised_reason": {
        "en": "The lane gave a reason this system does not recognise.",
        "es": "El carril ha dado un motivo que este sistema no reconoce.",
    },
    "operator_case.lane_unavailable": {
        "en": "The lane could not be reached.", "es": "No se ha podido contactar con el carril.",
    },
    "operator_case.standalone": {
        "en": "This intercom has no lane.", "es": "Este interfono no tiene carril.",
    },
    "operator_case.nothing_to_do": {
        "en": "Nothing was outstanding at this lane.",
        "es": "No había nada pendiente en este carril.",
    },
    # --- the operator's menu ----------------------------------------------
    "menu.open_now": {
        "en": "Press 1 to authorise opening.", "es": "Pulse 1 para autorizar la apertura.",
    },
    "menu.open_and_flag": {
        "en": "Press 2 to authorise opening and flag it.",
        "es": "Pulse 2 para autorizar la apertura y marcarla.",
    },
    "menu.do_not_open": {
        "en": "Press 3 to refuse.", "es": "Pulse 3 para denegar.",
    },
    "menu.hold": {
        "en": "Press 4 to hold.", "es": "Pulse 4 para esperar.",
    },
    "menu.transfer": {
        "en": "Press 5 to transfer.", "es": "Pulse 5 para transferir.",
    },
    "menu.call_back": {
        "en": "Press 6 to record a call-back number.",
        "es": "Pulse 6 para registrar un número de devolución de llamada.",
    },
    "operator.menu_repeat": {
        "en": "I did not get that. Please try again.",
        "es": "No lo he recogido. Inténtelo de nuevo.",
    },
    "operator.menu_callback_number": {
        "en": "Key the call-back number, then hash.",
        "es": "Marque el número de devolución de llamada y luego almohadilla.",
    },
    # Played to the HUMAN, at the moment they key an authorisation this version
    # can only record. It is one fixed sentence and it is not optional.
    "operator.cannot_open": {
        "en": "Recorded. This version cannot operate the barrier.",
        "es": "Registrado. Esta versión no puede accionar la barrera.",
    },
}


def missing_text(driver_languages, operator_language) -> tuple[str, ...]:
    """Every `(line, language)` this repository has no words for.

    Returned rather than raised, so a startup refusal can name all of them at
    once: an installer who is told about one missing language at a time restarts
    the process once per line.
    """
    missing = []
    for line in DRIVER_LINES:
        for language in driver_languages:
            if not TEXT.get(line, {}).get(language):
                missing.append(f"{line} [{language}]")
    for line in OPERATOR_LINES:
        if not TEXT.get(line, {}).get(operator_language):
            missing.append(f"{line} [{operator_language}]")
    return tuple(missing)


def audio_name(line: str, language: str) -> str:
    """The file a line's audio lives in, DERIVED from the key and the language.

    `case.plate_unclear` in `es` is `es/case.plate_unclear.wav`, and there is no
    table mapping lines to filenames -- a table would be a second copy of the
    line set, and the copy is the one that goes stale.
    """
    return f"{language}/{line}.wav"


__all__ = [
    "DRIVER_LINES",
    "LINES",
    "OPERATOR_LINES",
    "SHIPPED_LANGUAGES",
    "TEXT",
    "audio_name",
    "missing_text",
]

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

from .contract import (
    OPENING_AUTHORISATIONS,
    VEND_REFUSALS,
    AgentCase,
    Authorisation,
)

#: The lines the DRIVER hears, in every declared driver language.
#: Derived: one per case, one per authorisation, one MORE per authorisation
#: that can act, plus the four the dialogue itself needs.
DRIVER_LINES: tuple[str, ...] = (
    *(f"case.{case.value}" for case in AgentCase),
    *(f"authorisation.{value.value}" for value in Authorisation),
    #: THE SAME AUTHORISATION, SPOKEN AT A DOOR THAT WILL ASK FOR SOMETHING.
    #: Derived from `OPENING_AUTHORISATIONS` -- the act table itself -- so an
    #: authorisation that becomes an act cannot get a route to a barrier and
    #: keep the sentence that says it has none. Which of the two a driver hears
    #: is decided per call, at the moment the agent knows whether THIS door
    #: will ask: `Agent._say_authorisation`, the same branch that decides
    #: whether the person hears `operator.cannot_open`.
    *(f"authorisation.{value.value}.acting" for value in OPENING_AUTHORISATIONS),
    "driver.human_unreachable",
    "driver.nothing_usable",
    #: The person ANSWERED and then put the phone down before keying anything.
    #: A separate line from `nothing_usable`, because "I could not take an
    #: instruction" is not what happened and a driver told it goes on holding.
    "driver.operator_hung_up",
    "driver.hold_reprompt",
    #: What a driver is told once their press has been taken as a confirmation,
    #: and what the lane then said. **`commanded`, never `opened`**: the boom is
    #: `no_source` on the lane's own health surface, so nothing in this estate
    #: has measured a barrier moving.
    "ticket.confirmed",
    "ticket.vend_commanded",
    "ticket.vend_refused",
    #: What a driver is told when the code went up WHILE THEY WERE ON THE PHONE.
    #: The screen is where a driver looks, and it is enough on its own -- until
    #: they are already in a call, at which point nothing has told them the code
    #: is behind them. Without this, the press that followed confirmed a ticket
    #: they had never seen and never photographed, and they drove in with a stay
    #: whose only identity was a reference nobody held.
    "ticket.on_screen",
)

#: The line for a refusal code this build has no words for. Named here so the
#: agent, the line set and the test that keys them one-to-one all read the same
#: string -- and so that "unknown" cannot quietly become one of the lane's own
#: codes without the test noticing.
UNKNOWN_REFUSAL = "operator.vend_refused.unknown"

#: The lines the OPERATOR hears, in the operator language only.
OPERATOR_LINES: tuple[str, ...] = (
    *(f"operator_case.{case.value}" for case in AgentCase),
    *(f"menu.{value.value}" for value in Authorisation),
    "operator.menu_repeat",
    "operator.menu_callback_number",
    "operator.cannot_open",
    "operator.vend_commanded",
    #: SAID BEFORE THE CODE'S OWN SENTENCE, on every refused vend. A person
    #: briefed as an ordinary case, with no line saying a ticket was confirmed
    #: and refused, is a person who has to ask the driver over an intercom at
    #: three in the morning -- and who is then offered `OPEN_NOW`, which will
    #: meet the same refusal.
    "operator.ticket_refused",
    #: Why this call was already in progress when the person picked up.
    "operator.help_after_ticket",
    #: ONE SENTENCE PER PUBLISHED REFUSAL CODE, derived from the set rather than
    #: listed: a code the lane can answer and this build has no words for would
    #: be a person told "it was refused" and not told what to do about it.
    *(f"operator.vend_refused.{code}" for code in VEND_REFUSALS),
    #: AND THE ONE FOR A CODE THAT IS NOT IN THAT SET. The set above is OUR
    #: lane's, checked equal to its enum in both directions -- which is the
    #: right check for our lane and no check at all for the third-party seat
    #: this module is built to sit in (SETTLED 1). A foreign lane answers its
    #: own vocabulary, and the person used to be told nothing at all about the
    #: refusal: no sentence existed, so `()` was passed and the briefing was an
    #: ordinary case.
    UNKNOWN_REFUSAL,
)

LINES: tuple[str, ...] = DRIVER_LINES + OPERATOR_LINES

#: The lines that are DRAWN rather than spoken, one per declared driver
#: language, on the display beside the ticket.
#:
#: **They are a separate set from `LINES` and they have no audio.** A sentence
#: on a screen and a sentence in a call are different things: nobody plays this
#: one, `scripts/build_audio.py` does not generate a file for it, and the
#: manifest has no row for it. Folding it into `LINES` would have produced a WAV
#: nothing plays and a licence row for a recording nobody hears.
DISPLAY_LINES: tuple[str, ...] = ("display.instruction",)

#: **UPPER CASE, and it is a decision.** The font this package ships draws upper
#: case only -- see `font.py` for why, and the short version is that half a font
#: is half the drawing and half the review. A driver reads this through a
#: windscreen at a few metres, which is the case upper case is better at
#: anyway. A character the font cannot draw is a STARTUP REFUSAL naming the line
#: and the language, never a blank on a screen.
DISPLAY_TEXT: dict[str, dict[str, str]] = {
    "display.instruction": {
        "en": "TAKE A PHOTO OF THIS CODE, THEN PRESS THE BUTTON",
        "es-ES": "HAGA UNA FOTO DE ESTE CÓDIGO Y PULSE EL BOTÓN",
    },
}

#: The languages this repository ships text and audio for. A site may declare
#: only these; anything else is refused at startup with this list in the message,
#: because a language declared with no lines behind it is a silence with a
#: configuration key in front of it.
#:
#: **`es-ES`, not `es-ES`.** The Spanish here is Castilian and says so in the key:
#: `matrícula`, `aparcamiento`, `almohadilla`, `Pulse`. Shipped under a generic
#: `es-ES`, a garage in Texas or Bogotá would declare "Spanish", get this, and hear
#: several words that are wrong for its drivers -- a register chosen for them by
#: a package that never asked. A regional tag is the one thing that makes the
#: difference visible in the site's own configuration file.
SHIPPED_LANGUAGES: tuple[str, ...] = ("en", "es-ES")


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
        "es-ES": "Hay una avería en esta entrada. Le paso con una persona.",
    },
    # NEVER an instruction about the plate. The identification service is off,
    # and telling somebody to clean a plate nothing looked at is the failure
    # this whole module was reordered around.
    "case.identification_unavailable": {
        "en": "Our vehicle identification is not working right now. "
              "I am connecting you to a person.",
        "es-ES": "Nuestra identificación de vehículos no funciona ahora mismo. "
              "Le paso con una persona.",
    },
    "case.plate_not_read": {
        "en": "We could not read your number plate. If something is covering it, please "
              "clear it. I am connecting you to a person.",
        "es-ES": "No hemos podido leer su matrícula. Si algo la tapa, despéjela por favor. "
              "Le paso con una persona.",
    },
    "case.plate_unclear": {
        "en": "We could not read your number plate clearly. If something is covering it, "
              "please clear it. I am connecting you to a person.",
        "es-ES": "No hemos podido leer su matrícula con claridad. Si algo la tapa, despéjela "
              "por favor. Le paso con una persona.",
    },
    "case.vehicle_not_recognised": {
        "en": "We do not recognise this vehicle. I am connecting you to a person.",
        "es-ES": "No reconocemos este vehículo. Le paso con una persona.",
    },
    "case.rules_unavailable": {
        "en": "We cannot check this entrance's rules right now. "
              "I am connecting you to a person.",
        "es-ES": "No podemos comprobar las normas de esta entrada ahora mismo. "
              "Le paso con una persona.",
    },
    "case.entry_refused": {
        "en": "This vehicle was not admitted. I am connecting you to a person.",
        "es-ES": "Este vehículo no ha sido admitido. Le paso con una persona.",
    },
    "case.vehicle_not_detected": {
        "en": "The entrance did not detect a vehicle. I am connecting you to a person.",
        "es-ES": "La entrada no ha detectado ningún vehículo. Le paso con una persona.",
    },
    "case.entry_not_confirmed": {
        "en": "We could not confirm that you drove through. "
              "I am connecting you to a person.",
        "es-ES": "No hemos podido confirmar que haya pasado. Le paso con una persona.",
    },
    "case.stale_decision": {
        "en": "The last thing this entrance recorded is too old to be about you. "
              "I am connecting you to a person.",
        "es-ES": "Lo último que registró esta entrada es demasiado antiguo para referirse a "
              "usted. Le paso con una persona.",
    },
    "case.unrecognised_reason": {
        "en": "We cannot tell what happened here. I am connecting you to a person.",
        "es-ES": "No sabemos qué ha ocurrido aquí. Le paso con una persona.",
    },
    "case.lane_unavailable": {
        "en": "We cannot reach this entrance right now. I am connecting you to a person.",
        "es-ES": "No podemos comunicar con esta entrada ahora mismo. Le paso con una persona.",
    },
    "case.standalone": {
        "en": "You are through to the parking intercom. I am connecting you to a person.",
        "es-ES": "Ha llamado al interfono del aparcamiento. Le paso con una persona.",
    },
    "case.nothing_to_do": {
        "en": "This entrance has nothing outstanding for you. Goodbye.",
        "es-ES": "Esta entrada no tiene nada pendiente para usted. Adiós.",
    },
    # --- what the driver is told the human decided ------------------------
    # NEITHER PAIR SAYS THE BARRIER IS OPENING, and the pair is why there are
    # two of each. Until round 7 nothing here could move a barrier, so one
    # sentence was true everywhere: "this system cannot open it, wait for
    # them". A door with an act token can now ask, and at that door the same
    # sentence is a lie the driver hears immediately before "the barrier has
    # been asked to open" -- two sentences contradicting each other, the false
    # one first. So the clause that claims no route lives ONLY on the keys
    # spoken where there is none, and the `.acting` keys say what is true at a
    # door that will ask: a person authorised it, and nothing about a boom.
    # What happens next is `ticket.vend_commanded`'s to say, once something has
    # actually been asked.
    "authorisation.open_now": {
        "en": "A person has authorised your entry. This system cannot open the barrier "
              "itself, so please wait for them.",
        "es-ES": "Una persona ha autorizado su entrada. Este sistema no puede abrir la barrera "
              "por sí mismo; espere a que lo hagan.",
    },
    "authorisation.open_now.acting": {
        "en": "A person has authorised your entry.",
        "es-ES": "Una persona ha autorizado su entrada.",
    },
    "authorisation.open_and_flag": {
        "en": "A person has authorised your entry and made a note of it. This system cannot "
              "open the barrier itself, so please wait for them.",
        "es-ES": "Una persona ha autorizado su entrada y lo ha anotado. Este sistema no puede "
              "abrir la barrera por sí mismo; espere a que lo hagan.",
    },
    "authorisation.open_and_flag.acting": {
        "en": "A person has authorised your entry and made a note of it.",
        "es-ES": "Una persona ha autorizado su entrada y lo ha anotado.",
    },
    "authorisation.do_not_open": {
        "en": "A person has decided not to open the barrier.",
        "es-ES": "Una persona ha decidido no abrir la barrera.",
    },
    "authorisation.hold": {
        "en": "Please hold. Somebody is dealing with this.",
        "es-ES": "Espere, por favor. Alguien se está ocupando de esto.",
    },
    "authorisation.transfer": {
        "en": "I am putting you through to somebody else.",
        "es-ES": "Le paso con otra persona.",
    },
    "authorisation.call_back": {
        "en": "A person will call you back.",
        "es-ES": "Una persona le devolverá la llamada.",
    },
    # --- what the driver is told when nothing worked ----------------------
    "driver.human_unreachable": {
        "en": "Nobody answered. Please try this intercom again, or use the help number at "
              "this entrance.",
        "es-ES": "No ha contestado nadie. Vuelva a llamar por este interfono o use el número de "
              "ayuda de esta entrada.",
    },
    "driver.nothing_usable": {
        "en": "I could not take an instruction. Please try this intercom again.",
        "es-ES": "No he podido recoger una instrucción. Vuelva a llamar por este interfono.",
    },
    "driver.operator_hung_up": {
        "en": "The call to the person ended before they gave an instruction. "
              "Please try this intercom again.",
        "es-ES": "La llamada con la persona ha terminado antes de que diera una instrucción. "
              "Vuelva a llamar por este interfono.",
    },
    "driver.hold_reprompt": {
        "en": "Please keep holding. Somebody is dealing with this.",
        "es-ES": "Siga esperando, por favor. Alguien se está ocupando de esto.",
    },
    # --- what the operator is told the case is ----------------------------
    # The LANE NAME is not here. It cannot be: a name is a site's value and no
    # sentence in this repository can say it. The site declares one audio file
    # per intercom saying where the call is from, and the agent plays that
    # first -- see `[intercoms.<uri>] name_audio`.
    "operator_case.malfunction_active": {
        "en": "A fault is active at this lane.", "es-ES": "Hay una avería activa en este carril.",
    },
    "operator_case.identification_unavailable": {
        "en": "Vehicle identification is unavailable.",
        "es-ES": "La identificación de vehículos no está disponible.",
    },
    "operator_case.plate_not_read": {
        "en": "The number plate was not read.", "es-ES": "No se ha leído la matrícula.",
    },
    "operator_case.plate_unclear": {
        "en": "The number plate was unclear.", "es-ES": "La matrícula no estaba clara.",
    },
    "operator_case.vehicle_not_recognised": {
        "en": "The vehicle was not recognised.", "es-ES": "El vehículo no ha sido reconocido.",
    },
    "operator_case.rules_unavailable": {
        "en": "The entrance rules could not be checked.",
        "es-ES": "No se han podido comprobar las normas de la entrada.",
    },
    "operator_case.entry_refused": {
        "en": "Entry was refused.", "es-ES": "Se ha denegado la entrada.",
    },
    "operator_case.vehicle_not_detected": {
        "en": "The lane detected no vehicle.",
        "es-ES": "El carril no ha detectado ningún vehículo.",
    },
    "operator_case.entry_not_confirmed": {
        "en": "The entry was not confirmed.", "es-ES": "No se ha confirmado la entrada.",
    },
    "operator_case.stale_decision": {
        "en": "The lane's last decision is older than this site allows.",
        "es-ES": "La última decisión del carril es más antigua de lo que permite esta "
              "instalación.",
    },
    "operator_case.unrecognised_reason": {
        "en": "The lane gave a reason this system does not recognise.",
        "es-ES": "El carril ha dado un motivo que este sistema no reconoce.",
    },
    "operator_case.lane_unavailable": {
        "en": "The lane could not be reached.", "es-ES": "No se ha podido contactar con el carril.",
    },
    "operator_case.standalone": {
        "en": "This intercom has no lane.", "es-ES": "Este interfono no tiene carril.",
    },
    "operator_case.nothing_to_do": {
        "en": "Nothing was outstanding at this lane.",
        "es-ES": "No había nada pendiente en este carril.",
    },
    # --- the operator's menu ----------------------------------------------
    "menu.open_now": {
        "en": "Press 1 to authorise opening.", "es-ES": "Pulse 1 para autorizar la apertura.",
    },
    "menu.open_and_flag": {
        "en": "Press 2 to authorise opening and flag it.",
        "es-ES": "Pulse 2 para autorizar la apertura y marcarla.",
    },
    "menu.do_not_open": {
        "en": "Press 3 to refuse.", "es-ES": "Pulse 3 para denegar.",
    },
    "menu.hold": {
        "en": "Press 4 to hold.", "es-ES": "Pulse 4 para esperar.",
    },
    "menu.transfer": {
        "en": "Press 5 to transfer.", "es-ES": "Pulse 5 para transferir.",
    },
    "menu.call_back": {
        "en": "Press 6 to record a call-back number.",
        "es-ES": "Pulse 6 para registrar un número de devolución de llamada.",
    },
    "operator.menu_repeat": {
        "en": "I did not get that. Please try again.",
        "es-ES": "No lo he recogido. Inténtelo de nuevo.",
    },
    "operator.menu_callback_number": {
        "en": "Key the call-back number, then hash.",
        "es-ES": "Marque el número de devolución de llamada y luego almohadilla.",
    },
    # Played to the HUMAN when nothing at this door can act: no act token for
    # its lane, or a standalone intercom with no relay. It is one fixed sentence
    # and it is not optional -- a person who believes a barrier moved when it did
    # not is worse off than one who was never called.
    "operator.cannot_open": {
        "en": "Recorded. This entrance is not set up for this system to open it.",
        "es-ES": "Registrado. Esta entrada no está configurada para que este sistema la abra.",
    },
    # --- the ticket, to the driver ----------------------------------------
    # NEVER "the barrier is opening". The lane commands its relay and nothing in
    # this estate has measured a boom moving: `boom_did_not_rise` is `no_source`
    # on the lane's own health surface.
    "ticket.confirmed": {
        "en": "Thank you. I have your ticket.",
        "es-ES": "Gracias. Tengo su ticket.",
    },
    "ticket.vend_commanded": {
        "en": "The barrier has been asked to open. Please drive forward when it does.",
        "es-ES": "Se ha pedido que se abra la barrera. Avance cuando lo haga.",
    },
    "ticket.vend_refused": {
        "en": "The entrance did not accept that. I am connecting you to a person.",
        "es-ES": "La entrada no lo ha aceptado. Le paso con una persona.",
    },
    # Said IN THE CALL, about a code that went up during it. It says where to
    # look, what to do, and that the button is what completes it -- because the
    # press is what binds the ticket to this arrival.
    "ticket.on_screen": {
        "en": "There is a code on the screen. Take a photo of it, then press the button "
              "again.",
        "es-ES": "Hay un código en la pantalla. Hágale una foto y pulse el botón otra vez.",
    },
    # --- the ticket, to the operator --------------------------------------
    "operator.vend_commanded": {
        "en": "The barrier has been asked to open.",
        "es-ES": "Se ha pedido que se abra la barrera.",
    },
    # ALWAYS, on any refused vend, before whatever the code's own sentence is.
    "operator.ticket_refused": {
        "en": "This driver's ticket was refused by the entrance.",
        "es-ES": "La entrada ha rechazado el ticket de este conductor.",
    },
    "operator.help_after_ticket": {
        "en": "This driver was given a ticket a moment ago and has called back.",
        "es-ES": "A este conductor se le ha dado un ticket hace un momento y ha vuelto a "
              "llamar.",
    },
    # ONE PER PUBLISHED REFUSAL CODE. Each says what the lane refused and what
    # the person can do about it, because "it was refused" is a sentence that
    # leaves somebody standing at a barrier with nothing to try.
    "operator.vend_refused.no_vehicle": {
        "en": "The entrance says there is no vehicle on the loop.",
        "es-ES": "La entrada dice que no hay ningún vehículo en el lazo.",
    },
    "operator.vend_refused.malfunction_active": {
        "en": "The entrance has a fault that stops it opening safely.",
        "es-ES": "La entrada tiene una avería que le impide abrir con seguridad.",
    },
    "operator.vend_refused.geometry_incomplete": {
        "en": "Only one of the entrance loops is covered.",
        "es-ES": "Solo uno de los lazos de la entrada está cubierto.",
    },
    "operator.vend_refused.decision_in_future": {
        "en": "The entrance's clock disagrees with this system's.",
        "es-ES": "El reloj de la entrada no coincide con el de este sistema.",
    },
    "operator.vend_refused.decision_stale": {
        "en": "The entrance says this is too old to act on.",
        "es-ES": "La entrada dice que esto es demasiado antiguo para actuar.",
    },
    "operator.vend_refused.decision_mismatch": {
        "en": "The entrance has moved on to another vehicle.",
        "es-ES": "La entrada ya ha pasado a otro vehículo.",
    },
    "operator.vend_refused.already_completed": {
        "en": "This entry has already been opened once.",
        "es-ES": "Esta entrada ya se ha abierto una vez.",
    },
    "operator.vend_refused.not_completable": {
        "en": "The entrance has nothing outstanding to open for.",
        "es-ES": "La entrada no tiene nada pendiente que abrir.",
    },
    "operator.vend_refused.busy": {
        "en": "The entrance is already opening for somebody.",
        "es-ES": "La entrada ya se está abriendo para alguien.",
    },
    # AND THE REFUSAL THIS BUILD HAS NO WORDS FOR. It says exactly that and
    # claims nothing else: a lane that is not ours has its own vocabulary, and
    # guessing at which of ours it meant would tell a person to do something
    # about a fault this build invented for them.
    UNKNOWN_REFUSAL: {
        "en": "The entrance gave a reason I have no words for.",
        "es-ES": "La entrada ha dado un motivo para el que no tengo palabras.",
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


def missing_display_text(driver_languages) -> tuple[str, ...]:
    """Every `(line, language)` this repository has no DISPLAY words for.

    The same shape and the same reason as `missing_text`: an installer told
    about one missing language at a time restarts the process once per line.
    """
    return tuple(
        f"{line} [{language}]"
        for line in DISPLAY_LINES
        for language in driver_languages
        if not DISPLAY_TEXT.get(line, {}).get(language)
    )


def undrawable_display_text(driver_languages) -> tuple[str, ...]:
    """Every display line the FONT cannot draw, with the characters it lacks.

    A separate question from having the words: a line can exist and still hold a
    character this package has no glyph for, and that would be a hole in a frame
    at three in the morning rather than a refusal at installation.
    """
    from .font import missing as missing_glyphs

    out = []
    for line in DISPLAY_LINES:
        for language in driver_languages:
            text = DISPLAY_TEXT.get(line, {}).get(language)
            if not text:
                continue
            absent = missing_glyphs(text)
            if absent:
                out.append(
                    f"{line} [{language}]: no glyph for "
                    + ", ".join(repr(one) for one in absent)
                )
    return tuple(out)


def audio_name(line: str, language: str) -> str:
    """The file a line's audio lives in, DERIVED from the key and the language.

    `case.plate_unclear` in `es-ES` is `es-ES/case.plate_unclear.wav`, and there is no
    table mapping lines to filenames -- a table would be a second copy of the
    line set, and the copy is the one that goes stale.
    """
    return f"{language}/{line}.wav"


__all__ = [
    "DISPLAY_LINES",
    "DISPLAY_TEXT",
    "DRIVER_LINES",
    "LINES",
    "OPERATOR_LINES",
    "SHIPPED_LANGUAGES",
    "TEXT",
    "audio_name",
    "missing_display_text",
    "missing_text",
    "undrawable_display_text",
]
